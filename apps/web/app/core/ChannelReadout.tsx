/**
 * The cockpit's channel telemetry (M18.1, ADR-0065).
 *
 * The Minimal mode shows the structure and says what state it is in; the
 * cockpit additionally prints the numbers the structure was drawn from. Every
 * row is a channel on `VisualIntent` printed as it is — no rounding to a
 * friendlier figure, no bar for a channel that is a count, and the two
 * "known?" flags stated as words. This is what lets the owner check the
 * geometry against its evidence, which is the whole of the renderer's claim.
 *
 * Rendered in both the 3D and the 2D views, because it is markup, not scene.
 */

import { CHANNEL_LABEL } from "../lib/uistate/labels";
import type { VisualIntent } from "../lib/uistate/visual";

export type ChannelReadoutProps = {
  intent: VisualIntent;
};

const CHANNELS = Object.keys(CHANNEL_LABEL) as Array<keyof typeof CHANNEL_LABEL>;

function fixed(value: number): string {
  return value.toFixed(2);
}

export default function ChannelReadout({ intent }: ChannelReadoutProps) {
  return (
    <div className="channel-readout" data-channel-readout data-core-kind={intent.kind}>
      <h3 className="panel-title">Kanallar</h3>
      <dl className="channel-list">
        {CHANNELS.map((channel) => (
          <div key={channel} className="channel-row" data-channel={channel} data-value={fixed(intent[channel])}>
            <dt>{CHANNEL_LABEL[channel]}</dt>
            <dd>
              <span className="channel-bar" aria-hidden>
                <span className="channel-bar-fill" style={{ width: `${Math.max(0, Math.min(1, intent[channel])) * 100}%` }} />
              </span>
              <code>{fixed(intent[channel])}</code>
            </dd>
          </div>
        ))}
        <div className="channel-row" data-channel="constellationNodes" data-value={intent.constellationNodes}>
          <dt>Takımyıldız düğümleri</dt>
          <dd>
            <code>{intent.constellationNodes}</code>
            {intent.constellationNodes > 0 && (
              <span className="muted"> {intent.sourceNodesKnown ? "sayıldı" : "sabit temsil"}</span>
            )}
          </dd>
        </div>
        <div className="channel-row" data-channel="fieldNodes" data-value={intent.fieldNodes}>
          <dt>Aday alanı</dt>
          <dd>
            <code>{intent.fieldNodes}</code>
            <span className="muted"> {intent.fieldNodesKnown ? "sayıldı" : "bildirilmedi"}</span>
          </dd>
        </div>
        <div className="channel-row" data-channel="capabilityNodes" data-value={intent.capabilityNodes}>
          <dt>Yetenek düğümleri</dt>
          <dd>
            <code>{intent.capabilityNodes}</code>
            {intent.capabilityNodes > 0 && (
              <span className="muted"> {intent.capabilityNodesCounted ? "sayıldı" : "bir aday"}</span>
            )}
          </dd>
        </div>
        <div className="channel-row" data-channel="eyeActive" data-value={intent.eyeActive}>
          <dt>Göz açıklığı</dt>
          <dd>
            <code>{intent.eyeActive}</code>
          </dd>
        </div>
      </dl>
    </div>
  );
}
