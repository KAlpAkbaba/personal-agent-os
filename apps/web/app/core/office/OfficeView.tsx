/**
 * The whole Ofis page body as a pure function of props: top bar, scene, panel, approvals.
 * `page.tsx` owns the data, the poll and the selection; this renders them.
 */

import Link from "next/link";

import OfficePanel from "./OfficePanel";
import OfficeScene from "./OfficeScene";
import type { OfficeView as Office } from "./officeApi";
import { buildOffice, buildPanel } from "./officeModel";

export default function OfficeView({
  view,
  selected,
  offline,
  reducedMotion,
  onSelect,
}: {
  view: Office;
  selected: string | null;
  offline: boolean;
  reducedMotion: boolean;
  onSelect: (seat: string) => void;
}) {
  const office = buildOffice(view);
  const bar = office.topBar;
  return (
    <div className="office" data-office="root">
      <div className="office-topbar" data-office="topbar">
        <span>
          Döngü: <strong>{bar.cycleId}</strong>
        </span>
        <span>Başlangıç: {bar.startedAt}</span>
        <span>{bar.runningAgents}</span>
        <span>{bar.estimated}</span>
        <span>Max limit: {bar.limit}</span>
        {offline && (
          <span className="office-offline" role="status">
            bağlantı yok
          </span>
        )}
      </div>
      <div className="office-main">
        <OfficeScene
          seats={office.seats}
          selected={selected}
          reducedMotion={reducedMotion}
          onSelect={onSelect}
        />
        <OfficePanel panel={selected ? buildPanel(view, selected) : null} />
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
