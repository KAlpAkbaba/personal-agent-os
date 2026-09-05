"use client";

/**
 * The thin control strip: where you are, whether the picture is live, and the
 * two knobs the owner has (quality tier, 2D view).
 *
 * The connection indicator is not decoration. It is the difference between "the
 * agent is calm" and "we stopped hearing from it", which the geometry alone
 * cannot express — a still core looks the same either way.
 */

import Link from "next/link";

import { SignOutButton } from "../components/OwnerGate";
import { QUALITY_TIERS, type QualityTier, TIER_LABEL } from "../lib/uistate/quality";
import type { Connection } from "../lib/uistate/truth";

const CONNECTION_TEXT: Record<Connection["kind"], string> = {
  connecting: "bağlanıyor…",
  live: "canlı",
  unreachable: "ulaşılamıyor",
  unauthorized: "oturum reddedildi",
  contract_mismatch: "sözleşme sürümü uyuşmuyor",
};

export type CoreBarProps = {
  mode: "minimal" | "cockpit";
  connection: Connection;
  tier: QualityTier;
  onTier: (tier: QualityTier) => void;
  force2d: boolean;
  onForce2d: (value: boolean) => void;
  onRefresh: () => void;
};

export default function CoreBar({
  mode,
  connection,
  tier,
  onTier,
  force2d,
  onForce2d,
  onRefresh,
}: CoreBarProps) {
  return (
    <div className="core-bar">
      <strong>Ajan Çekirdeği</strong>

      <span className="core-connection" data-connection={connection.kind}>
        {CONNECTION_TEXT[connection.kind]}
        {connection.kind === "unreachable" && ` — ${connection.error}`}
        {connection.kind === "contract_mismatch" && ` (v${connection.version})`}
      </span>

      <span className="spacer" />

      {QUALITY_TIERS.map((option) => (
        <button
          key={option}
          type="button"
          className="core-chip"
          aria-pressed={tier === option}
          onClick={() => onTier(option)}
          data-tier-option={option}
        >
          {TIER_LABEL[option]}
        </button>
      ))}

      <button
        type="button"
        className="core-chip"
        aria-pressed={force2d}
        onClick={() => onForce2d(!force2d)}
        data-force-2d={force2d ? "yes" : "no"}
      >
        2B
      </button>

      <button type="button" className="core-chip" onClick={onRefresh}>
        Yenile
      </button>

      <Link href={mode === "minimal" ? "/core/cockpit" : "/core"}>
        {mode === "minimal" ? "Kokpit →" : "← Sade"}
      </Link>
      <Link href="/">Ana sayfa</Link>
      <SignOutButton />
    </div>
  );
}
