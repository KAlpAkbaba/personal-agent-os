"use client";

import { useCallback, useEffect, useState } from "react";

import {
  UnauthorizedError,
  currentSession,
  getToken,
  signIn,
  signOut,
  subscribe,
} from "../lib/session";

/**
 * The web shell's owner sign-in.
 *
 * One owner, so this is not a login form in the usual sense: there is no user
 * to identify, only authority to prove. The owner pastes the credential minted
 * once by `POST /v1/identity/bootstrap` (or rotated on the host), it is
 * exchanged for a session, and the credential itself is discarded.
 *
 * `OwnerGate` renders its children only while a session exists. Any 401 from
 * anywhere in the shell clears the session, which brings this panel straight
 * back — the same coarse "authenticate again" the API intends.
 */

function SignInPanel({
  onSignedIn,
}: {
  onSignedIn: () => void;
}) {
  const [credential, setCredential] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(async () => {
    const value = credential.trim();
    if (!value || busy) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(value);
      // Drop the credential from component state the moment it is spent.
      setCredential("");
      onSignedIn();
    } catch (err) {
      setError(
        err instanceof UnauthorizedError
          ? "Kimlik bilgisi kabul edilmedi."
          : err instanceof Error
            ? err.message
            : String(err),
      );
    } finally {
      setBusy(false);
    }
  }, [credential, busy, onSignedIn]);

  return (
    <main>
      <h1>Giriş</h1>
      <p className="subtitle">
        Sahip kimlik bilgisini yapıştır. Bir kez kullanılır, saklanmaz.
      </p>
      <div className="panel">
        <input
          type="password"
          value={credential}
          onChange={(e) => setCredential(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          placeholder="pagentos_ok_…"
          aria-label="Sahip kimlik bilgisi"
          autoComplete="off"
          spellCheck={false}
          style={{
            width: "100%",
            padding: "0.6rem 0.8rem",
            borderRadius: 8,
            border: "1px solid #232734",
            background: "#0f1115",
            color: "var(--text)",
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          }}
        />
        <button
          onClick={submit}
          disabled={busy || !credential.trim()}
          style={{
            marginTop: "0.75rem",
            padding: "0.6rem 1.2rem",
            borderRadius: 8,
            border: "none",
            background: "var(--accent)",
            color: "#0f1115",
            fontWeight: 600,
            cursor: busy ? "progress" : "pointer",
            opacity: busy || !credential.trim() ? 0.6 : 1,
          }}
        >
          {busy ? "Doğrulanıyor…" : "Giriş"}
        </button>
        {error && (
          <p className="muted" style={{ marginTop: "0.75rem" }}>
            {error}
          </p>
        )}
        <p className="muted" style={{ marginTop: "1rem" }}>
          Kimlik bilgisi kayıpsa: sunucuda{" "}
          <code>python -m app.identity.recover --rotate</code>.
        </p>
      </div>
    </main>
  );
}

export function SignOutButton() {
  const [busy, setBusy] = useState(false);
  return (
    <button
      onClick={async () => {
        setBusy(true);
        await signOut();
        setBusy(false);
      }}
      style={{
        background: "none",
        border: "1px solid #232734",
        borderRadius: 8,
        color: "var(--muted)",
        cursor: "pointer",
        padding: "0.3rem 0.7rem",
        fontSize: "0.8rem",
      }}
    >
      {busy ? "…" : "Çıkış"}
    </button>
  );
}

export default function OwnerGate({
  children,
}: {
  children: React.ReactNode;
}) {
  // `null` = still deciding (localStorage is not readable during SSR), so the
  // panel never flashes before we know whether a session exists.
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);

  const check = useCallback(async () => {
    if (!getToken()) {
      setAuthenticated(false);
      return;
    }
    // A stored token can be revoked or expired; ask the API rather than trust it.
    setAuthenticated((await currentSession()) !== null);
  }, []);

  useEffect(() => {
    check();
    // Any 401 anywhere clears the token, which lands here.
    return subscribe((token) => setAuthenticated(token !== null ? true : false));
  }, [check]);

  if (authenticated === null) {
    return (
      <main>
        <p className="muted">Oturum kontrol ediliyor…</p>
      </main>
    );
  }
  if (!authenticated) {
    return <SignInPanel onSignedIn={check} />;
  }
  return <>{children}</>;
}
