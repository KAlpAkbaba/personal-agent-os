"use client";

/**
 * `/core/misheard` - 'Ne demek istemiştin?': the sentences the system did not understand
 * (ADR-0254's notebook), each waiting for the owner to say what was meant.
 *
 * Its own page for now; the proposal puts it in the Onay Merkezi and embedding it there is a
 * later card. The page holds the state and calls the actions; everything shown is
 * MisheardView's, everything decided is the Cloud Core's.
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../../components/FamilyPage";
import MisheardView from "./MisheardView";
import { fetchNotebook, type MisheardList } from "./misheardApi";
import { forgetNotebook, forgetRow, saveMeaning, type Notice } from "./misheardActions";

export default function MisheardPage() {
  const [list, setList] = useState<MisheardList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    const result = await fetchNotebook();
    if (result.ok) {
      setList(result.list);
      setError(null);
    } else {
      setError(result.message);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const run = async (action: () => Promise<Notice>, after?: () => void) => {
    setBusy(true);
    const result = await action();
    setBusy(false);
    setNotice(result.message);
    if (result.ok) after?.();
    void refresh();
  };

  return (
    <FamilyPage
      id="misheard"
      title="Ne demek istemiştin?"
      lead="Anlayamadığım cümleler, tanıyıcının yazdığı haliyle. Ne demek istediğini yazarsan bir dahaki sefere daha iyi anlarım."
    >
      <MisheardView
        list={list}
        error={error}
        drafts={drafts}
        notice={notice}
        busy={busy}
        onDraft={(id, text) => setDrafts((current) => ({ ...current, [id]: text }))}
        onSave={(id, text) =>
          void run(
            () => saveMeaning(id, text),
            () => setDrafts((current) => ({ ...current, [id]: "" })),
          )
        }
        onDelete={(id) => void run(() => forgetRow(id))}
        onForgetAll={() => void run(forgetNotebook)}
      />
    </FamilyPage>
  );
}
