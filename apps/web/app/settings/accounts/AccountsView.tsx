/**
 * What Ayarlar > Hesaplar shows - pure, so a test renders it to markup and reads it.
 *
 * Per account: the owner's name for it, the provider, the address, the state and the last
 * sync, with rename and disconnect. Above: a name box and the two buttons. Below: the exact
 * redirect URL to register and the steps for each provider - shown in full until that
 * provider's app registration is configured, because those steps are the owner's
 * (READY_FOR_OWNER) and nobody else can do them.
 */

import {
  type Account,
  type AccountProvider,
  type AccountsData,
  PROVIDER_LABEL,
  stateLabel,
  syncLabel,
} from "./accounts";

export type AccountsViewProps = {
  data: AccountsData | null;
  error: string | null;
  message: string | null;
  busy: boolean;
  newName: string;
  onNewName: (value: string) => void;
  onConnect: (provider: AccountProvider) => void;
  onRename: (account: Account) => void;
  onDisconnect: (account: Account) => void;
};

function SetupSteps({ provider, data }: { provider: AccountProvider; data: AccountsData }) {
  const setup = provider === "gmail" ? data.setup.google : data.setup.microsoft;
  return (
    <details data-setup={provider} open={!setup.configured}>
      <summary>
        {PROVIDER_LABEL[provider]} kurulumu {setup.configured ? "(hazır)" : "(sizin yapmanız gereken adımlar)"}
      </summary>
      <ol>
        {setup.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <p className="muted">
        <a href={setup.console_url} target="_blank" rel="noreferrer">
          Konsolu aç →
        </a>
      </p>
    </details>
  );
}

export function AccountsView(props: AccountsViewProps) {
  const { data, error, message, busy } = props;
  if (error) return <p data-accounts-error>{error}</p>;
  if (!data) return <p className="muted">yükleniyor…</p>;
  const ready = (provider: AccountProvider) =>
    data.setup.public_base_configured && (provider === "gmail" ? data.setup.google : data.setup.microsoft).configured;
  return (
    <section data-accounts>
      <h2>Bağlı hesaplar</h2>
      {data.accounts.length === 0 ? (
        <p className="muted" data-accounts-empty>
          Henüz bağlı hesap yok.
        </p>
      ) : (
        <ul className="detail-list">
          {data.accounts.map((account) => (
            <li key={account.id} className={account.state === "error" ? "detail-row tone-bad" : "detail-row"} data-account={account.name}>
              <span className="detail-head">{account.name}</span>
              <span className="muted detail-facts">
                {[PROVIDER_LABEL[account.provider] ?? account.provider, account.address, stateLabel(account), syncLabel(account)]
                  .filter(Boolean)
                  .join(" · ")}
              </span>
              <span className="approval-pair">
                <button type="button" className="core-chip" data-account-rename={account.id} disabled={busy} onClick={() => props.onRename(account)}>
                  Yeniden adlandır
                </button>
                <button type="button" className="core-chip" data-account-disconnect={account.id} disabled={busy} onClick={() => props.onDisconnect(account)}>
                  Bağlantıyı kes
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <h2>Hesap bağla</h2>
      <label>
        Hesabın adı{" "}
        <input
          data-account-name
          value={props.newName}
          maxLength={40}
          placeholder="ör. İş, Kişisel, Aktivra"
          onChange={(event) => props.onNewName(event.target.value)}
        />
      </label>{" "}
      {(["gmail", "microsoft"] as const).map((provider) => (
        <button
          key={provider}
          type="button"
          className="core-chip"
          data-connect={provider}
          disabled={busy || !ready(provider)}
          onClick={() => props.onConnect(provider)}
        >
          {PROVIDER_LABEL[provider]} bağla
        </button>
      ))}
      {message && <p data-accounts-message>{message}</p>}

      <h2>Kurulum</h2>
      <p>
        Google ve Microsoft&apos;a kaydedilecek dönüş adresi (redirect URI):{" "}
        <code data-redirect-uri>{data.setup.redirect_uri || "henüz yok - PAGENTOS_ACCOUNTS_PUBLIC_BASE_URL ayarlanmalı"}</code>
      </p>
      <SetupSteps provider="gmail" data={data} />
      <SetupSteps provider="microsoft" data={data} />
      <p className="muted">
        Parola istenmez ve saklanmaz: giriş Google/Microsoft sayfasında yapılır, Cloud Core yalnız şifreli bir erişim
        anahtarı tutar. Posta gönderme her zaman taslağı okuyup sizin onayınızla olur.
      </p>
    </section>
  );
}
