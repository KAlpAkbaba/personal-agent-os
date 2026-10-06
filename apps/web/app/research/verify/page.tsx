"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import OwnerGate, { SignOutButton } from "../../components/OwnerGate";
import { UnauthorizedError } from "../../lib/session";
import { type Verification, listVerifications, sinceForDays } from "./api";
import VerificationList from "./VerificationList";

/**
 * /research/verify - card verify-mode: what the owner asked to verify ("bunu
 * doğrula: ..."), the verdict, the sources with the quote that decides, and the
 * strongest counter-argument. Searchable by words and narrowed by time, the
 * same two ways he asks aloud ("Everest hakkında ne bulmuştuk", "geçen hafta
 * neyi doğrulamıştık"). A READ: nothing here starts a check - only his own
 * sentence does.
 */

const RANGES: { label: string; days: number | null }[] = [
  { label: "Tümü", days: null },
  { label: "Son 7 gün", days: 7 },
  { label: "Son 30 gün", days: 30 },
];

function VerifySurface() {
  const [items, setItems] = useState<Verification[]>([]);
  const [words, setWords] = useState("");
  const [days, setDays] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async (q: string, range: number | null) => {
    setLoading(true);
    try {
      setItems(await listVerifications({ q, since: sinceForDays(range) }));
      setError(null);
    } catch (err) {
      // A 401 is not an error to show: OwnerGate has already taken over.
      if (!(err instanceof UnauthorizedError)) {
        setError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh("", null);
  }, [refresh]);

  return (
    <main className="research">
      <header className="status-row">
        <h1>Doğrulamalar</h1>
        <Link href="/research">Araştırmalar</Link>
        <SignOutButton />
      </header>
      <form
        className="panel"
        onSubmit={(event) => {
          event.preventDefault();
          void refresh(words, days);
        }}
      >
        <input
          aria-label="İddiada geçen kelimeler"
          placeholder="Örn. Everest"
          value={words}
          onChange={(event) => setWords(event.target.value)}
        />
        <select
          aria-label="Zaman aralığı"
          value={days === null ? "" : String(days)}
          onChange={(event) => {
            const next = event.target.value === "" ? null : Number(event.target.value);
            setDays(next);
            void refresh(words, next);
          }}
        >
          {RANGES.map((range) => (
            <option key={range.label} value={range.days === null ? "" : String(range.days)}>
              {range.label}
            </option>
          ))}
        </select>
        <button type="submit" disabled={loading}>
          Ara
        </button>
      </form>
      {error ? <p className="error">{error}</p> : null}
      <VerificationList items={items} />
    </main>
  );
}

export default function VerifyPage() {
  return (
    <OwnerGate>
      <VerifySurface />
    </OwnerGate>
  );
}
