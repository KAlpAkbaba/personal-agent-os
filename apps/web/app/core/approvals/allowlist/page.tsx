"use client";

/**
 * `/core/approvals/allowlist` - the sites a cloud browser job may act on (ADR-0218).
 *
 * A cloud job reads everywhere and ACTS only on a site listed here; the list starts empty.
 * Adding a site is the one widening path, so the page says what a listed site allows, and
 * shows the server's reason when a site is refused (not a registrable domain, a bare suffix,
 * on the deny-list). Sites from the shared seed file are shown but cannot be removed here.
 */

import { useCallback, useEffect, useState } from "react";

import FamilyPage from "../../../components/FamilyPage";
import { addSite, fetchSites, removeSite, type AllowlistSite } from "./allowlistApi";

export default function AllowlistPage() {
  const [sites, setSites] = useState<AllowlistSite[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [site, setSite] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setSites(await fetchSites());
      setError(null);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "alınamadı");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const add = async () => {
    setBusy(true);
    const result = await addSite(site);
    setBusy(false);
    if (result.ok) {
      setAnswer(result.already_listed ? "Bu site zaten listede." : null);
      setSite("");
      void refresh();
    } else {
      setAnswer(result.message);
    }
  };

  const remove = async (name: string) => {
    setBusy(true);
    const result = await removeSite(name);
    setBusy(false);
    setAnswer(result.ok ? null : result.message);
    void refresh();
  };

  return (
    <FamilyPage
      id="approvals-allowlist"
      title="Bulut izin listesi"
      lead="Bulutta çalışan bir tarayıcı işi her yerde okur, ama yalnızca bu listedeki sitelerde işlem yapar (tıklar, doldurur, gönderir). Liste boş başlar."
    >
      {error && (
        <p role="alert">
          Liste alınamadı: {error} <button onClick={() => void refresh()}>Yeniden dene</button>
        </p>
      )}
      {!error && sites === null && <p className="muted">yükleniyor</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <input
          aria-label="Site (örn. magaza.com.tr)"
          placeholder="magaza.com.tr"
          value={site}
          onChange={(event) => setSite(event.target.value)}
        />
        <button type="submit" disabled={busy || site.trim() === ""}>
          Ekle
        </button>
      </form>
      {answer && <p role="alert">{answer}</p>}
      {sites && sites.length === 0 && <p className="muted">Listede site yok; bulut işleri hiçbir yerde işlem yapmaz.</p>}
      {sites && sites.length > 0 && (
        <ul className="detail-list">
          {sites.map((entry) => (
            <li key={entry.site} className="detail-row" data-site={entry.site}>
              <span className="detail-head">{entry.site}</span>
              <span className="muted">
                {entry.source === "seed" ? "paylaşılan dosyadan" : `eklendi ${entry.added_at}`}
              </span>
              <button
                type="button"
                disabled={busy || entry.source === "seed"}
                onClick={() => void remove(entry.site)}
              >
                Çıkar
              </button>
            </li>
          ))}
        </ul>
      )}
    </FamilyPage>
  );
}
