/**
 * The one pixel Minimal mode cannot do without.
 *
 * A still Core looks exactly the same whether the system is calm or whether we
 * stopped hearing from it, and the geometry has no way to express the
 * difference (ADR-0056 §1). The old chrome bar carried that sentence; the
 * full-viewport stage has no bar, so it is carried here: a dot with a word
 * beside it, at the top of the stage, in the same five states the bar had.
 *
 * It also carries the contract-version lag, when there is one. A Cloud Core
 * that still speaks v2 never publishes the alarm or the display states, and an
 * empty alarm cell on such a server means "this server cannot tell you",
 * not "no alarm is set".
 */

import type { Connection } from "../lib/uistate/truth";

const CONNECTION_TEXT: Record<Connection["kind"], string> = {
  connecting: "bağlanıyor…",
  live: "canlı",
  unreachable: "ulaşılamıyor",
  unauthorized: "oturum reddedildi",
  contract_mismatch: "sözleşme sürümü uyuşmuyor",
};

export type ConnectionDotProps = {
  connection: Connection;
  /** A sentence about an older-but-readable server contract, or `null`. */
  contractLag?: string | null;
};

export default function ConnectionDot({ connection, contractLag = null }: ConnectionDotProps) {
  return (
    <div className="core-connection-dot" data-connection={connection.kind} data-core-connection>
      <span className="core-dot" aria-hidden />
      <span className="core-connection-text">
        {CONNECTION_TEXT[connection.kind]}
        {connection.kind === "unreachable" && ` — ${connection.error}`}
        {connection.kind === "contract_mismatch" && ` (v${connection.version})`}
      </span>
      {contractLag && (
        <span className="core-connection-lag muted" data-contract-lag>
          {contractLag}
        </span>
      )}
    </div>
  );
}
