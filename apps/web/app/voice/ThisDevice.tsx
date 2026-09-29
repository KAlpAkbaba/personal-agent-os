"use client";

/**
 * "Bu bilgisayar" (ADR-0208): which enrolled computer this browser is on.
 *
 * A browser cannot tell which Cloud Core device it shares a machine with, and the owner has
 * two (home, office). Said once here and remembered per browser, it is sent when a voice
 * session opens, so "hesap makinesini aç" - no computer named - runs on the computer the
 * owner is sitting at. It is only a claim: Cloud Core ignores it for a device that is not
 * enrolled, online and able, and it never authorises anything.
 *
 * Split like the other cells: `ThisDeviceView` is markup as a pure function of its props
 * (rendered to static HTML by its test), `ThisDevice` owns the load and the storage.
 */

import { useCallback, useEffect, useState } from "react";

import { listDevices } from "../lib/research/api";
import { type DeviceInfo, PRESENCE_LABEL, presenceOf } from "../lib/research/model";
import { getThisDeviceId, setThisDeviceId } from "../lib/thisDevice";

export const THIS_DEVICE_NONE = "";

export const THIS_DEVICE_TITLE = "Bu bilgisayar";

export const THIS_DEVICE_HELP =
  "Cihaz adı söylemediğiniz komutlar (ör. 'hesap makinesini aç') bu bilgisayarda çalışır. " +
  "Yeni bağlantıda geçerli olur.";

export type ThisDeviceViewProps = {
  devices: DeviceInfo[];
  /** A device id, or `THIS_DEVICE_NONE` when the owner has not said. */
  value: string;
  onChange: (value: string) => void;
  loadError?: string | null;
};

export function ThisDeviceView({ devices, value, onChange, loadError }: ThisDeviceViewProps) {
  const selectable = devices.filter((d) => presenceOf(d) !== "revoked");
  // A remembered device that is no longer listed (revoked, or a different Cloud Core) is
  // still shown as chosen rather than silently reading "belirtilmedi".
  const known = selectable.some((d) => d.device_id === value);
  return (
    <div className="status-row" data-this-device={value || "none"}>
      <label className="muted">
        {THIS_DEVICE_TITLE}{" "}
        <select
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-label={THIS_DEVICE_TITLE}
        >
          <option value={THIS_DEVICE_NONE}>Belirtilmedi</option>
          {value !== THIS_DEVICE_NONE && !known && (
            <option value={value}>Kayıtlı cihaz listede yok</option>
          )}
          {selectable.map((d) => (
            <option key={d.device_id} value={d.device_id}>
              {d.name} ({PRESENCE_LABEL[presenceOf(d)]})
            </option>
          ))}
        </select>
      </label>
      <span className="muted" data-this-device-help>
        {loadError ? `Cihaz listesi alınamadı: ${loadError}` : THIS_DEVICE_HELP}
      </span>
    </div>
  );
}

export default function ThisDevice() {
  const [devices, setDevices] = useState<DeviceInfo[]>([]);
  const [value, setValue] = useState<string>(THIS_DEVICE_NONE);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    setValue(getThisDeviceId() ?? THIS_DEVICE_NONE);
    let cancelled = false;
    listDevices().then(
      (list) => {
        if (!cancelled) setDevices(list);
      },
      (error: unknown) => {
        if (!cancelled) setLoadError(error instanceof Error ? error.message : String(error));
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const change = useCallback((next: string) => {
    if (setThisDeviceId(next === THIS_DEVICE_NONE ? null : next)) setValue(next);
  }, []);

  return <ThisDeviceView devices={devices} value={value} onChange={change} loadError={loadError} />;
}
