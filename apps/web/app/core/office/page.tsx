"use client";

/**
 * `/core/office` - the Ofis: the agent team as a small pixel office.
 *
 * Polls `GET /v1/team/office` every 5 s (not while the tab is hidden) and keeps the last
 * good data on screen when a fetch fails - `bağlantı yok`, never a blank office. All
 * decisions live in `officeModel.ts` and `createOfficePoller`; this file only wires them.
 */

import { useEffect, useRef, useState } from "react";

import FamilyPage from "../../components/FamilyPage";
import OfficeView from "./OfficeView";
import { createOfficePoller, fetchOffice, type OfficeView as Office } from "./officeApi";
import { selectSeat } from "./officeModel";
import { arrivals } from "./officeMood";
import "./office.css";

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return reduced;
}

export default function OfficePage() {
  const [view, setView] = useState<Office | null>(null);
  const [offline, setOffline] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const reducedMotion = useReducedMotion();
  // the seats that just took a new task walk in to their desks for a moment
  const [arriving, setArriving] = useState<string[]>([]);
  const previous = useRef<Office | null>(null);

  useEffect(() => {
    const poller = createOfficePoller({
      fetch: fetchOffice,
      onData: (next) => {
        const walked = arrivals(previous.current, next);
        previous.current = next;
        if (walked.length > 0) {
          setArriving(walked);
          window.setTimeout(() => setArriving([]), 2600);
        }
        setView(next);
        setOffline(false);
        setFailure(null);
      },
      onError: (error) => {
        setOffline(true);
        setFailure(error instanceof Error ? error.message : "alınamadı");
      },
      isHidden: () => document.hidden,
      setInterval: (fn, ms) => window.setInterval(fn, ms),
      clearInterval: (id) => window.clearInterval(id as number),
    });
    const onVisibility = () => poller.visibilityChanged();
    document.addEventListener("visibilitychange", onVisibility);
    poller.start();
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      poller.stop();
    };
  }, []);

  return (
    <FamilyPage
      id="office"
      title="Ofis"
      lead="Ekibin şu an ne yaptığı: kim çalışıyor, kim bekliyor, kim size döndü."
    >
      {view === null && !offline && <p className="muted">yükleniyor</p>}
      {view === null && offline && (
        <p role="alert">
          bağlantı yok{failure ? `: ${failure}` : ""}
        </p>
      )}
      {view && (
        <OfficeView
          view={view}
          selected={selected}
          offline={offline}
          reducedMotion={reducedMotion}
          arriving={arriving}
          onSelect={(seat) => setSelected((current) => selectSeat(current, seat))}
        />
      )}
    </FamilyPage>
  );
}
