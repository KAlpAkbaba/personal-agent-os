"use client";

import {
  type DeviceInfo,
  PRESENCE_LABEL,
  type SelectionPreview,
  advertisesBrowser,
  aliasesOf,
  presenceOf,
} from "../lib/research/model";

/**
 * "Otomatik" (Cloud Core picks: explicit target → online → capability →
 * policy → healthiest) or one explicit device. The list is what
 * `GET /v1/devices` returned; the preview is what `POST /v1/devices/select`
 * answered for the current choice. Presentational — the page owns the calls.
 */

export const AUTO = "";

const PRESENCE_CLASS: Record<string, string> = {
  online: "ok",
  stale: "unknown",
  offline: "fail",
  revoked: "fail",
  unknown: "unknown",
};

export function PresenceBadge({ device }: { device: Pick<DeviceInfo, "presence" | "status"> }) {
  const presence = presenceOf(device);
  return (
    <span className={`badge ${PRESENCE_CLASS[presence]}`} data-presence={presence}>
      {PRESENCE_LABEL[presence]}
    </span>
  );
}

export type DeviceChooserProps = {
  devices: DeviceInfo[];
  value: string; // AUTO or a device_id
  onChange: (value: string) => void;
  preview: SelectionPreview | null;
  previewBusy?: boolean;
  onPreview: () => void;
  disabled?: boolean;
  loadError?: string | null;
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  gap: "0.6rem",
  alignItems: "center",
  padding: "0.35rem 0",
  flexWrap: "wrap",
};

export default function DeviceChooser({
  devices,
  value,
  onChange,
  preview,
  previewBusy,
  onPreview,
  disabled,
  loadError,
}: DeviceChooserProps) {
  return (
    <fieldset
      style={{ border: "1px solid #232734", borderRadius: 8, padding: "0.5rem 0.9rem", margin: 0 }}
      disabled={disabled}
    >
      <legend className="muted" style={{ padding: "0 0.3rem" }}>
        Cihaz
      </legend>

      <label style={rowStyle}>
        <input
          type="radio"
          name="device"
          value={AUTO}
          checked={value === AUTO}
          onChange={() => onChange(AUTO)}
        />
        <span>Otomatik</span>
        <span className="muted">— Cloud Core uygun cihazı seçer</span>
      </label>

      {devices.map((d) => {
        const chrome = advertisesBrowser(d);
        const aliases = aliasesOf(d);
        return (
          <label key={d.device_id} style={rowStyle} data-device-id={d.device_id}>
            <input
              type="radio"
              name="device"
              value={d.device_id}
              checked={value === d.device_id}
              onChange={() => onChange(d.device_id)}
            />
            <span style={{ fontWeight: 600 }}>{d.name}</span>
            <PresenceBadge device={d} />
            <span
              className={`badge ${chrome ? "ok" : "unknown"}`}
              data-chrome={chrome ? "yes" : "no"}
              title={chrome ? "browser.chrome yeteneğini duyuruyor" : "browser.chrome yeteneği yok"}
            >
              {chrome ? "Chrome var" : "Chrome yok"}
            </span>
            {aliases.length > 0 && (
              <span className="muted">takma ad: {aliases.join(", ")}</span>
            )}
            {d.platform && <span className="muted">{d.platform}</span>}
          </label>
        );
      })}

      {devices.length === 0 && !loadError && (
        <p className="muted" style={{ margin: "0.25rem 0" }}>
          Kayıtlı cihaz yok; "Otomatik" seçimi cihaz bulamayınca görev açıklamayla başarısız olur.
        </p>
      )}
      {loadError && (
        <p className="muted" style={{ margin: "0.25rem 0" }}>
          Cihaz listesi alınamadı: {loadError}
        </p>
      )}

      <div style={{ ...rowStyle, marginTop: "0.4rem" }}>
        <button
          type="button"
          onClick={onPreview}
          disabled={disabled || previewBusy}
          style={{
            background: "none",
            border: "1px solid #232734",
            borderRadius: 8,
            color: "var(--accent)",
            cursor: previewBusy ? "progress" : "pointer",
            padding: "0.3rem 0.7rem",
            fontSize: "0.85rem",
          }}
        >
          {previewBusy ? "Bakılıyor…" : "Hangi cihaz çalıştırır?"}
        </button>
        {preview && preview.kind === "device" && (
          <span className="selection-preview" data-selected-device={preview.device_id}>
            <span className="badge ok">{preview.name}</span>
            <span className="muted" style={{ marginLeft: "0.4rem" }}>
              bu görevi çalıştırır{preview.reason ? ` (${preview.reason})` : ""}
            </span>
          </span>
        )}
        {preview && preview.kind === "none" && (
          <span className="selection-preview muted" data-selected-device="">
            {preview.detail}
          </span>
        )}
      </div>
    </fieldset>
  );
}
