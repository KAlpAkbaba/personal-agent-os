"use client";

/**
 * `/memory/conversations` - the owner's conversations as text (conversation-transcripts):
 * read, search, delete, 'unut'; the people whose voices were named and their consent; the
 * standing 'evde dinle' switch.
 *
 * The page holds the state and calls the actions; everything shown is ConversationsView's,
 * everything decided is the Cloud Core's.
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../../components/FamilyPage";
import ConversationsView from "./ConversationsView";
import {
  deleteConversation,
  deletePerson,
  fetchConversation,
  fetchConversations,
  fetchHomeListen,
  fetchPeople,
  forgetAll,
  recordConsent,
  setHomeListen,
  type ConversationDetail,
  type ConversationItem,
  type Person,
  type Refusal,
} from "./conversationsApi";

export default function ConversationsPage() {
  const [list, setList] = useState<ConversationItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<ConversationDetail | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [homeListen, setHome] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async (q: string) => {
    const [listed, known, home] = await Promise.all([
      fetchConversations(q),
      fetchPeople(),
      fetchHomeListen(),
    ]);
    if (listed.ok) {
      setList(listed.items);
      setError(null);
    } else {
      setError(listed.message);
    }
    if (known.ok) setPeople(known.people);
    if (home.ok) setHome(home.on);
  }, []);

  useEffect(() => {
    void refresh("");
  }, [refresh]);

  const run = async (action: () => Promise<{ ok: true } | Refusal>, done: string) => {
    setBusy(true);
    const result = await action();
    setBusy(false);
    setNotice(result.ok ? done : result.message);
    if (selected) {
      const again = await fetchConversation(selected.id);
      setSelected(again.ok ? again.conversation : null);
    }
    void refresh(query);
  };

  return (
    <FamilyPage
      id="conversations"
      title="Konuşmalar"
      lead="Konuşmaların yazıya dökülmüş hali; kim ne dedi. Ses saklanmaz."
    >
      <ConversationsView
        list={list}
        error={error}
        query={query}
        selected={selected}
        people={people}
        homeListen={homeListen}
        notice={notice}
        busy={busy}
        onQuery={setQuery}
        onSearch={() => void refresh(query)}
        onOpen={(id) =>
          void fetchConversation(id).then((result) =>
            result.ok ? setSelected(result.conversation) : setNotice(result.message),
          )
        }
        onDelete={(id) => {
          if (selected?.id === id) setSelected(null);
          void run(() => deleteConversation(id), "Silindi.");
        }}
        onForgetAll={() => {
          setSelected(null);
          void run(forgetAll, "Bütün konuşmalar unutuldu.");
        }}
        onHomeListen={(on) => void run(() => setHomeListen(on), on ? "Evde dinliyorum." : "Evde dinlemiyorum.")}
        onConsent={(id) => void run(() => recordConsent(id), "İzni kaydettim.")}
        onDeletePerson={(id) => void run(() => deletePerson(id), "Kişi ve ses profili silindi.")}
      />
    </FamilyPage>
  );
}
