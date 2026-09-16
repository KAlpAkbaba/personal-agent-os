import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import DeviceChooser, { AUTO } from "../../app/research/DeviceChooser";
import ProgressPanel from "../../app/research/ProgressPanel";
import { DEVICES, taskAt } from "./fixtures";

const noop = () => {};

describe("DeviceChooser", () => {
  it("offers Otomatik first and lists every device with presence, Chrome and aliases", () => {
    const html = renderToStaticMarkup(
      <DeviceChooser devices={DEVICES} value={AUTO} onChange={noop} preview={null} onPreview={noop} />,
    );
    expect(html.indexOf("Otomatik")).toBeLessThan(html.indexOf("Ev bilgisayarı"));
    expect(html).toContain('checked="" value=""');
    for (const d of DEVICES) expect(html).toContain(`data-device-id="${d.device_id}"`);
    expect(html).toContain('data-presence="online"');
    expect(html).toContain("çevrimiçi");
    expect(html).toContain('data-presence="stale"');
    expect(html).toContain("yanıt gecikiyor");
    expect(html).toContain('data-presence="offline"'); // legacy status-only device
    expect(html).toContain("çevrimdışı");
    expect(html.match(/data-chrome="yes"/g)?.length).toBe(2);
    expect(html.match(/data-chrome="no"/g)?.length).toBe(1);
    expect(html).toContain("takma ad: ev, masaüstü");
    expect(html).toContain("Hangi cihaz çalıştırır?");
  });

  it("marks the explicit choice and shows the selection preview", () => {
    const html = renderToStaticMarkup(
      <DeviceChooser
        devices={DEVICES}
        value="dev-home"
        onChange={noop}
        preview={{ kind: "device", device_id: "dev-home", name: "Ev bilgisayarı", reason: "explicit" }}
        onPreview={noop}
      />,
    );
    expect(html).toContain('checked="" value="dev-home"');
    expect(html).not.toContain('checked="" value=""');
    expect(html).toContain('data-selected-device="dev-home"');
    expect(html).toContain("bu görevi çalıştırır (explicit)");
  });

  it("shows the no-device answer and the empty/failed list states", () => {
    const none = renderToStaticMarkup(
      <DeviceChooser
        devices={[]}
        value={AUTO}
        onChange={noop}
        preview={{ kind: "none", detail: "Uygun cihaz yok." }}
        onPreview={noop}
      />,
    );
    expect(none).toContain('data-selected-device=""');
    expect(none).toContain("Uygun cihaz yok.");
    expect(none).toContain("Kayıtlı cihaz yok");
    const failed = renderToStaticMarkup(
      <DeviceChooser devices={[]} value={AUTO} onChange={noop} preview={null} onPreview={noop} loadError="HTTP 503" />,
    );
    expect(failed).toContain("Cihaz listesi alınamadı: HTTP 503");
    expect(failed).not.toContain("Kayıtlı cihaz yok");
  });
});

describe("ProgressPanel", () => {
  it("shows the Turkish stage, counters, device, events and a cancel button while running", () => {
    const html = renderToStaticMarkup(<ProgressPanel task={taskAt("fetching")} onCancel={noop} />);
    expect(html).toContain('data-stage="fetching"');
    expect(html).toContain('data-terminal="no"');
    expect(html).toContain("Sayfalar Chrome ile getiriliyor…");
    expect(html).toContain("sorgu: 4 / 9");
    expect(html).toContain("getirilen: 3 / 12");
    expect(html).toContain("başarısız: 1");
    expect(html).toContain("kanıt: 2");
    expect(html).toContain('data-device-id="dev-home"');
    expect(html).toContain("İptal et");
    // Events newest first, each with its Turkish stage label.
    expect(html.indexOf("Sayfalar Chrome ile getiriliyor")).toBeLessThan(html.indexOf("Cihaz seçiliyor"));
    expect(html.indexOf("Cihaz seçiliyor")).toBeLessThan(html.indexOf("Planlandı"));
    expect(html).toContain("9 sorgu");
  });

  it("offers Duraklat while running, Devam et while paused, and neither once terminal (B31)", () => {
    const running = renderToStaticMarkup(
      <ProgressPanel task={taskAt("fetching")} onCancel={noop} onPause={noop} onResume={noop} />,
    );
    expect(running).toContain('data-action="pause"');
    expect(running).toContain("Duraklat");
    expect(running).not.toContain('data-action="resume"');
    expect(running).not.toContain("duraklatıldı");

    const paused = renderToStaticMarkup(
      <ProgressPanel
        task={taskAt("fetching", { progress: { fetch_done: 3, fetch_total: 12, paused: true } })}
        onCancel={noop}
        onPause={noop}
        onResume={noop}
      />,
    );
    expect(paused).toContain('data-paused="yes"');
    expect(paused).toContain("Araştırma duraklatıldı");
    expect(paused).toContain('data-action="resume"');
    expect(paused).toContain("Devam et");
    expect(paused).not.toContain('data-action="pause"');
    // The stage is still the stage: pausing is a flag, not a place.
    expect(paused).toContain('data-stage="fetching"');

    const ready = renderToStaticMarkup(
      <ProgressPanel task={taskAt("ready")} onCancel={noop} onPause={noop} onResume={noop} />,
    );
    expect(ready).not.toContain('data-action="pause"');
    expect(ready).not.toContain('data-action="resume"');
  });

  it("drops the cancel button and announces readiness on terminal stages", () => {
    const ready = renderToStaticMarkup(<ProgressPanel task={taskAt("ready")} onCancel={noop} />);
    expect(ready).toContain('data-terminal="yes"');
    expect(ready).toContain("Hazır");
    expect(ready).not.toContain("Hazır…");
    expect(ready).not.toContain("İptal et");
    expect(ready).toContain("Araştırma tamamlandı");
    expect(ready).toContain("artifact hazır");

    const failed = renderToStaticMarkup(
      <ProgressPanel
        task={taskAt("failed", { device: null, error: { code: "no_capable_device", message: "Uygun cihaz yok." } })}
        onCancel={noop}
      />,
    );
    expect(failed).toContain("Başarısız");
    expect(failed).toContain("Uygun cihaz yok.");
    expect(failed).toContain("henüz seçilmedi");
    expect(failed).not.toContain("İptal et");

    const cancelled = renderToStaticMarkup(<ProgressPanel task={taskAt("cancelled")} onCancel={noop} />);
    expect(cancelled).toContain("İptal edildi");
    expect(cancelled).not.toContain("İptal et<");
  });

  it("reports a transient poll error without hiding the last known state", () => {
    const html = renderToStaticMarkup(<ProgressPanel task={taskAt("ranking")} pollError="ağ koptu" />);
    expect(html).toContain("Kaynaklar sıralanıyor…");
    expect(html).toContain("Durum alınamadı, yeniden denenecek: ağ koptu");
  });

  it("shows a prominent Turkish notice while waiting for the owner and keeps polling", () => {
    const html = renderToStaticMarkup(
      <ProgressPanel
        task={taskAt("waiting_for_owner_verification", {
          progress: {
            queries_total: 9, queries_done: 4, discovered: 12, fetch_total: 12, fetch_done: 3,
            fetch_failed: 1, evidence: 2, verification_url: "https://www.google.com/sorry/index",
          },
        })}
        onCancel={noop}
      />,
    );
    expect(html).toContain('data-stage="waiting_for_owner_verification"');
    // Not terminal: the cancel button and the "…" progress suffix both stay.
    expect(html).toContain('data-terminal="no"');
    expect(html).toContain("Sahibin doğrulaması bekleniyor…");
    expect(html).toContain("İptal et");
    expect(html).toContain(
      "Google bir doğrulama sayfası gösterdi. Chrome penceresi öne getirildi; sayfayı " +
        "tamamlayın, araştırma otomatik olarak devam eder.",
    );
    expect(html).toContain('href="https://www.google.com/sorry/index"');
    expect(html).toContain("Doğrulama sayfasını aç");
  });

  it("omits the verification link when the run has not surfaced one yet", () => {
    const html = renderToStaticMarkup(
      <ProgressPanel task={taskAt("waiting_for_owner_verification")} onCancel={noop} />,
    );
    expect(html).toContain("Google bir doğrulama sayfası gösterdi");
    expect(html).not.toContain("Doğrulama sayfasını aç");
  });
});
