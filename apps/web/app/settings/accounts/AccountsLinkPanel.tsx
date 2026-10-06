/**
 * The `/settings` panel that leads to Ayarlar > Hesaplar (card mail-accounts-connect,
 * inspector's 3rd return: the page existed and nothing linked to it).
 */

import Link from "next/link";

export const ACCOUNTS_PAGE_HREF = "/settings/accounts";

export function AccountsLinkPanel() {
  return (
    <section className="panel" data-panel="accounts-link">
      <h3 className="panel-title">
        <span>Hesaplar</span>
        <span className="panel-count">e-posta ve takvim</span>
      </h3>
      <p className="muted">
        Gmail ve Microsoft 365 hesaplarınızı adlarıyla (Kişisel, İş, …) parola vermeden
        bağlayın; posta ve takvim her hesabı adıyla söyler.
      </p>
      <Link href={ACCOUNTS_PAGE_HREF} className="core-chip" data-accounts-link="yes">
        Hesapları yönet
      </Link>
    </section>
  );
}
