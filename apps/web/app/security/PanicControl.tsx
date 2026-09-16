"use client";

/**
 * B25 req 660: the kill control, where the owner can find it.
 *
 * `POST /v1/identity/panic` has existed since B05 and the matrix's note on it is three
 * words long: *Arayüzde görünmüyor*. A kill switch nobody can find is not a kill switch;
 * it is a line in SECURITY_MODEL §10. The one moment it exists for — "something is wrong
 * and I want everything holding a session to stop holding one" — is the worst possible
 * moment to be reading documentation about how to `curl` it.
 *
 * Three properties, and each is there for a reason the other two do not cover:
 *
 * * **Two deliberate steps.** One click arms it and names exactly what will happen; the
 *   second does it. An emergency control reachable by a single stray click is a control
 *   the owner will disable.
 * * **It says what it does NOT do.** It revokes sessions, not the owner credential: the
 *   owner signs back in on a device they still hold. Without that sentence the honest
 *   fear is "will this lock me out of my own system", and a control people are afraid of
 *   is a control they do not press when they should.
 * * **It revokes this session too, and says so.** The page will drop to the sign-in gate
 *   the instant it succeeds. That is the correct behaviour and a surprise if unannounced.
 */

import { useCallback, useState } from "react";

import { apiFetch, UnauthorizedError } from "../lib/session";

export const PANIC_PATH = "/v1/identity/panic";

type Phase =
  | { kind: "idle" }
  | { kind: "armed" }
  | { kind: "busy" }
  | { kind: "done"; revoked: number }
  | { kind: "failed"; error: string };

export default function PanicControl() {
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  const fire = useCallback(async () => {
    setPhase({ kind: "busy" });
    try {
      const response = await apiFetch(PANIC_PATH, { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = (await response.json()) as { revoked?: number };
      setPhase({ kind: "done", revoked: typeof body.revoked === "number" ? body.revoked : 0 });
    } catch (err) {
      // A 401 here is the control WORKING: this session was one of the ones revoked, and
      // the shell's own handler is already returning the owner to the sign-in gate.
      if (err instanceof UnauthorizedError) {
        setPhase({ kind: "done", revoked: 0 });
        return;
      }
      setPhase({ kind: "failed", error: err instanceof Error ? err.message : String(err) });
    }
  }, []);

  return (
    <section className="panel attention" data-panel="panic" id="panic">
      <h3 className="panel-title">
        <span>Acil durdurma</span>
        <span className="panel-count">SECURITY_MODEL §10</span>
      </h3>

      <p className="muted">
        Açık olan <strong>bütün oturumları</strong> iptal eder — bu tarayıcınınki dahil.
        Sahip kimlik bilgisi geçerli kalır; elinizde duran bir cihazdan tekrar giriş
        yapabilirsiniz. Kimlik bilgisinin kendisinden şüpheleniyorsanız sunucuda{" "}
        <code>python -m app.identity.recover --rotate</code> ile döndürün.
      </p>

      {phase.kind === "idle" && (
        <button
          type="button"
          className="panic-button"
          data-panic="arm"
          onClick={() => setPhase({ kind: "armed" })}
        >
          Bütün oturumları iptal et…
        </button>
      )}

      {phase.kind === "armed" && (
        <div className="panic-confirm" data-panic="armed">
          <p>
            Emin misiniz? Bu sayfa dahil her oturum kapanacak ve giriş ekranına
            döneceksiniz.
          </p>
          <button type="button" className="panic-button" data-panic="fire" onClick={() => void fire()}>
            Evet, hepsini iptal et
          </button>
          <button type="button" className="core-chip" data-panic="cancel" onClick={() => setPhase({ kind: "idle" })}>
            Vazgeç
          </button>
        </div>
      )}

      {phase.kind === "busy" && (
        <p className="panel-empty" data-panic="busy" aria-live="polite">
          İptal ediliyor…
        </p>
      )}

      {phase.kind === "done" && (
        <p className="muted" data-panic="done" aria-live="polite">
          {phase.revoked > 0
            ? `${phase.revoked} oturum iptal edildi.`
            : "Oturumlar iptal edildi."}{" "}
          Tekrar giriş yapmanız gerekiyor.
        </p>
      )}

      {phase.kind === "failed" && (
        <p className="panel-unknown" data-panic="failed" aria-live="polite">
          İptal edilemedi: {phase.error}. Sunucuda{" "}
          <code>python -m app.identity.recover --rotate</code> hâlâ çalışır.
        </p>
      )}
    </section>
  );
}
