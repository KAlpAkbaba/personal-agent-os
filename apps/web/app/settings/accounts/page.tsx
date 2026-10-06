"use client";

/**
 * `/settings/accounts` - Ayarlar > Hesaplar (card mail-accounts-connect, the owner
 * 2026-10-05: "e-posta bağlamayı arayüzden ver, Gmail ve M365 bağlayayım, 3 hesap,
 * isimlendirmek istiyorum").
 *
 * The owner names an account, presses "Gmail bağla" or "Microsoft 365 bağla", signs in at
 * the provider, and comes back to an account voice can name ("İş hesabında 3 yeni posta").
 * Rename and disconnect are one press each; disconnect asks once in the browser because it
 * revokes the provider's consent and cannot be undone from here.
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../../components/FamilyPage";
import {
  type Account,
  type AccountProvider,
  type AccountsData,
  connectAccount,
  disconnectAccount,
  fetchAccounts,
  renameAccount,
} from "./accounts";
import { AccountsView } from "./AccountsView";

export default function AccountsPage() {
  const [data, setData] = useState<AccountsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [newName, setNewName] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await fetchAccounts());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onConnect = useCallback(
    async (provider: AccountProvider) => {
      setBusy(true);
      const result = await connectAccount(provider, newName);
      if (result.ok) {
        window.location.assign(result.url);
        return;
      }
      setMessage(result.speech);
      setBusy(false);
    },
    [newName],
  );

  const onRename = useCallback(
    async (account: Account) => {
      const name = window.prompt(`'${account.name}' için yeni ad`, account.name);
      if (!name || name.trim() === account.name) return;
      setBusy(true);
      setMessage((await renameAccount(account.id, name)).speech);
      await load();
      setBusy(false);
    },
    [load],
  );

  const onDisconnect = useCallback(
    async (account: Account) => {
      if (!window.confirm(`'${account.name}' hesabının bağlantısı kesilsin mi?`)) return;
      setBusy(true);
      setMessage((await disconnectAccount(account.id)).speech);
      await load();
      setBusy(false);
    },
    [load],
  );

  return (
    <FamilyPage
      id="settings-accounts"
      title="Hesaplar"
      lead="E-posta ve takvim hesaplarınız: Gmail ve Microsoft 365, her biri sizin verdiğiniz adla."
    >
      <AccountsView
        data={data}
        error={error}
        message={message}
        busy={busy}
        newName={newName}
        onNewName={setNewName}
        onConnect={(provider) => void onConnect(provider)}
        onRename={(account) => void onRename(account)}
        onDisconnect={(account) => void onDisconnect(account)}
      />
    </FamilyPage>
  );
}
