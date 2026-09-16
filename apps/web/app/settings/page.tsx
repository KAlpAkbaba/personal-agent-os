"use client";

/**
 * `/settings` — B24 req 699.
 *
 * A settings page for this product is not a form, and saying why matters more than the
 * page does. Almost nothing here is set by the web shell: alarms, routines, presence,
 * evolution and experience are configured through the ONE voice router that gates them,
 * and a second surface that could change the same rules would be a second authority —
 * exactly what ADR-0053 §5 refuses. What the owner has never had is a place to READ the
 * rules the running system is actually applying. Six subsystems publish a `/policy` route
 * saying precisely that, and nothing in the product had ever asked one.
 *
 * So: the settings that genuinely live in this browser are editable here (they are stored
 * in this device's `localStorage` and affect nothing else), and everything else is shown
 * as what the Cloud Core reports, with the surface that owns it named. The one exception
 * is the ambient panel's four switches (B48, ADR-0155 decision 5): they PUT the same
 * owner-gated policy the voice tool writes, so they add a control, not an authority.
 */

import { useCallback } from "react";

import FamilyPage, { Row, Rows } from "../components/FamilyPage";
import {
  type AmbientToggle,
  type CameraMode,
  fetchAmbientPolicy,
  fetchDeviceStatus,
  fetchVoiceQualification,
  updateAmbientCameraMode,
  updateAmbientPolicy,
} from "../lib/cockpit/api";
import { fetchPolicy } from "../lib/pages/detail";
import { useLoaded, useNow } from "../lib/pages/useLoaded";
import { QUALITY_TIERS, TIER_LABEL } from "../lib/uistate/quality";
import { AmbientPanel, VoiceQualificationPanel } from "../core/panels/CockpitPanels";
import { useCorePreferences } from "../core/usePreferences";

/** Each policy route, and the surface that actually sets what it reports. */
const POLICIES: { id: string; title: string; path: string; ownedBy: string }[] = [
  {
    id: "routines-policy",
    title: "Rutinler",
    path: "/v1/routines/policy",
    ownedBy: "sesle kurulur; /routines sayfasında listelenir",
  },
  {
    id: "alarms-policy",
    title: "Alarmlar",
    path: "/v1/alarms/policy",
    ownedBy: "sesle kurulur; /alarms sayfasında listelenir",
  },
  {
    id: "presence-policy",
    title: "Varlık ve göz",
    path: "/v1/presence/policy",
    ownedBy: "cihaz tarafı; kamera daima sahibin açık iznine bağlı",
  },
  {
    id: "evolution-policy",
    title: "Kendini geliştirme",
    path: "/v1/evolution/policy",
    ownedBy: "/selfdev sayfası; yükseltme sahibin onayına bağlı",
  },
  {
    id: "experience-policy",
    title: "Deneyim ve dersler",
    path: "/v1/experience/policy",
    ownedBy: "/selfdev sayfası",
  },
  {
    id: "selfmodel-policy",
    title: "Öz model",
    path: "/v1/selfmodel/policy",
    ownedBy: "sistemin kendi taraması",
  },
];

function PolicySection({ id, title, path, ownedBy }: (typeof POLICIES)[number]) {
  const state = useLoaded(useCallback(() => fetchPolicy(path), [path]));
  return (
    <Rows
      id={id}
      title={`${title} kuralları`}
      state={state.state}
      empty={`Bu Cloud Core ${title.toLocaleLowerCase("tr-TR")} kurallarını bildirmiyor.`}
      badge={(rows) => `${rows.length} · ${ownedBy}`}
      onRetry={state.refresh}
    >
      {(entry) => (
        <Row key={entry.name} keyText={`${id}:${entry.name}`} head={entry.name} facts={[entry.value]} />
      )}
    </Rows>
  );
}

export default function SettingsPage() {
  const now = useNow();
  const { tier, setTier, force2d, setForce2d } = useCorePreferences();
  const ambient = useLoaded(useCallback(() => fetchAmbientPolicy(), []));
  const devices = useLoaded(useCallback(() => fetchDeviceStatus(), []));
  const qualification = useLoaded(useCallback(() => fetchVoiceQualification(), []));
  const refreshAmbient = ambient.refresh;
  const toggleAmbient = useCallback(
    (field: AmbientToggle, value: boolean) => void updateAmbientPolicy(field, value).then(refreshAmbient),
    [refreshAmbient],
  );
  const chooseCameraMode = useCallback(
    (mode: CameraMode) => void updateAmbientCameraMode(mode).then(refreshAmbient),
    [refreshAmbient],
  );

  return (
    <FamilyPage
      id="settings"
      title="Ayarlar"
      lead="Bu tarayıcıya ait olanlar değiştirilebilir; geri kalanı çalışan sistemin bildirdiği kurallardır."
    >
      <section className="panel" data-panel="render-preferences">
        <h3 className="panel-title">
          <span>Bu cihazdaki görüntü</span>
          <span className="panel-count">yalnızca bu tarayıcı</span>
        </h3>
        <p className="muted">
          Bu iki seçim bu tarayıcının belleğinde durur; Cloud Core&apos;a gitmez, başka bir
          cihazı etkilemez. Çekirdek makinenin gerçekten ürettiği kareleri ölçüp gerekirse
          bir kademe aşağı iner (B23 req 722) — buradaki seçim tavandır, taban değil.
        </p>
        <div className="setting-row">
          <span>Görüntü kademesi</span>
          <span>
            {QUALITY_TIERS.map((option) => (
              <button
                key={option}
                type="button"
                className="core-chip"
                aria-pressed={option === tier}
                data-tier-option={option}
                onClick={() => setTier(option)}
              >
                {TIER_LABEL[option]}
              </button>
            ))}
          </span>
        </div>
        <div className="setting-row">
          <span>2B görünüm</span>
          <button
            type="button"
            className="core-chip"
            aria-pressed={force2d}
            data-force-2d={force2d ? "yes" : "no"}
            onClick={() => setForce2d(!force2d)}
          >
            {force2d ? "açık" : "kapalı"}
          </button>
        </div>
      </section>

      <AmbientPanel
        policy={ambient.state}
        devices={devices.state}
        onToggle={toggleAmbient}
        onCameraMode={chooseCameraMode}
        always
      />
      <VoiceQualificationPanel state={qualification.state} now={now} always />

      {POLICIES.map((policy) => (
        <PolicySection key={policy.id} {...policy} />
      ))}
    </FamilyPage>
  );
}
