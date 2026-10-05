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
import {
  applySetting,
  createOfficePoller,
  fetchOffice,
  putModels,
  type ModelId,
  type ModelRole,
  type ModelSetting,
  type OfficeView as Office,
} from "./officeApi";
import { chooseModel, selectSeat } from "./officeModel";
import { arrivals } from "./officeMood";
import { fetchTestRoom, testRoomFromBoard, type TestSeat } from "./officeTestRoom";
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
  // The setting the owner just chose, drawn before the poll brings it back (optimistic).
  const [chosen, setChosen] = useState<ModelSetting | null>(null);
  const [modelNotice, setModelNotice] = useState<string | null>(null);
  const saving = useRef(false);
  const reducedMotion = useReducedMotion();
  // the seats that just took a new task walk in to their desks for a moment
  const [arriving, setArriving] = useState<string[]>([]);
  const lastAnswer = useRef<Office | null>(null);
  // the test team's room, read from the board with every office poll
  const [testSeats, setTestSeats] = useState<TestSeat[]>(() => testRoomFromBoard([], new Date()));

  useEffect(() => {
    const poller = createOfficePoller({
      fetch: fetchOffice,
      onData: (next) => {
        const walked = arrivals(lastAnswer.current, next);
        lastAnswer.current = next;
        if (walked.length > 0) {
          setArriving(walked);
          window.setTimeout(() => setArriving([]), 2600);
        }
        setView(next);
        void fetchTestRoom().then(setTestSeats);
        // The poll's setting takes over once it is the stored one (or a newer one).
        setChosen((mine) =>
          mine && !saving.current && next.models && next.models.updated_at >= mine.updated_at
            ? null
            : mine,
        );
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

  const shown = view && chosen ? { ...view, models: chosen } : view;

  const save = (next: ModelSetting) => {
    const previous = shown?.models;
    if (!previous || saving.current) return;
    saving.current = true;
    void applySetting(previous, next, {
      put: putModels,
      show: setChosen,
      say: setModelNotice,
    }).finally(() => {
      saving.current = false;
    });
  };

  const onChooseModel = (role: ModelRole, model: ModelId) => {
    if (!shown?.models) return;
    const choice = chooseModel(shown.models, role, model);
    if ("refusal" in choice) setModelNotice(choice.refusal);
    else save(choice.setting);
  };

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
      {shown && (
        <OfficeView
          view={shown}
          selected={selected}
          offline={offline}
          reducedMotion={reducedMotion}
          arriving={arriving}
          onSelect={(seat) => setSelected((current) => selectSeat(current, seat))}
          modelNotice={modelNotice}
          onChooseModel={onChooseModel}
          onToggleFallback={save}
          testSeats={testSeats}
        />
      )}
    </FamilyPage>
  );
}
