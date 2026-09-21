"use client";

/**
 * `/gods-eye` — owner addition 3 of 2026-09-21 (docs/DECISIONS.md ADR-0197).
 *
 * God's Eye View (MIT, bilawalsidhu/gods-eye-view) is served by the Cloud Core's aux
 * workload on the tailnet (`NEXT_PUBLIC_GODS_EYE_URL`, set by
 * scripts/voice/start-web-voice.ps1 to `http://<broker host>:4173/`). This page is the
 * shell's door to it: the globe inline, and the same URL in a new tab for the full
 * window. By voice: "Dünya gözünü aç" opens it in the owner's own Chrome.
 *
 * Nothing of the owner's passes through here: the iframe is a plain cross-origin
 * document; the shell neither reads it nor hands it a credential.
 */

import FamilyPage from "../components/FamilyPage";

export const GODS_EYE_URL: string =
  process.env.NEXT_PUBLIC_GODS_EYE_URL?.trim() || "http://pagentos-core:4173/";

export default function GodsEyePage() {
  return (
    <FamilyPage
      id="gods-eye"
      title="Dünya Gözü"
      lead="Canlı dünya görünümü: uçaklar, gemiler, uydular, depremler ve kameralar, 3B bir dünya üzerinde. Sesle: “Dünya gözünü aç.”"
    >
      <p className="family-page-actions">
        <a href={GODS_EYE_URL} target="_blank" rel="noopener noreferrer" data-gods-eye-open>
          Yeni sekmede aç ↗
        </a>
      </p>
      {/* oxlint-disable react/iframe-missing-sandbox -- a CROSS-origin document:
          allow-same-origin only grants it its OWN origin (Cesium keeps settings in its
          storage and talks to its own server), and no top-navigation of this shell is
          granted; without allow-scripts there is no globe at all. */}
      <iframe
        src={GODS_EYE_URL}
        title="God's Eye View"
        data-gods-eye-frame
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-pointer-lock"
        allow="fullscreen; geolocation; microphone"
        style={{ width: "100%", height: "78vh", border: 0, borderRadius: 12, background: "#000" }}
      />
      {/* oxlint-enable react/iframe-missing-sandbox */}
    </FamilyPage>
  );
}
