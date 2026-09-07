/**
 * The Digital Operator panel and the Core's readout for the operator states
 * (M19 spec §4): sentences about published transitions, never a promise that
 * a click worked.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DigitalOperatorPanel } from "../../app/core/panels/CockpitPanels";
import StateReadout from "../../app/core/StateReadout";
import { OPERATOR_STEP_TTL_MS } from "../../app/lib/uistate/contract";
import { applyResponse, emptyTruth } from "../../app/lib/uistate/truth";
import { visualFor } from "../../app/lib/uistate/visual";
import {
  AGENT_IDLE,
  OPERATOR_FAILED,
  OPERATOR_FAILED_TASK,
  OPERATOR_RUNNING,
  OPERATOR_RUNNING_BARE,
  OPERATOR_RUNNING_INDEXED,
  OPERATOR_VERIFYING,
  T0,
  event,
  resetSequence,
  response,
} from "../uistate/fixtures";

function truthOf(events: ReturnType<typeof event>[], at = T0) {
  resetSequence();
  return applyResponse(emptyTruth(), response(events), at);
}

function panel(events: ReturnType<typeof event>[], now = T0) {
  return renderToStaticMarkup(<DigitalOperatorPanel truth={truthOf(events)} now={now} />);
}

function readout(events: ReturnType<typeof event>[], compact = false, now = T0) {
  return renderToStaticMarkup(<StateReadout intent={visualFor(truthOf(events), now)} compact={compact} />);
}

describe("the Digital Operator panel", () => {
  it("is empty, in words, before the operator ever ran", () => {
    const html = panel([AGENT_IDLE()]);
    expect(html).toContain('data-panel="digital-operator"');
    expect(html).toContain('data-panel-empty="yes"');
    expect(html).toContain('data-operator-stage="none"');
    expect(html).toContain('data-operator-last-known=""');
    expect(html).toContain("Operatör henüz çalışmadı.");
    expect(html).not.toContain("attention");
  });

  it("states the running step, the capability, the observed window, the goal and the age", () => {
    const html = panel([OPERATOR_RUNNING("open_notepad", "app.launch", "Adsız - Not Defteri")], T0 + 3_000);
    expect(html).toContain('data-panel-empty="no"');
    expect(html).toContain('data-operator-stage="running"');
    expect(html).toContain('data-operator-step="open_notepad"');
    expect(html).toContain('data-operator-capability="app.launch"');
    expect(html).toContain('data-operator-window="Adsız - Not Defteri"');
    expect(html).toContain("Operatör çalışıyor");
    expect(html).toContain("adım: open_notepad · yetenek: app.launch · pencere: Adsız - Not Defteri");
    // The publisher's label, bare: the panel does not say whether it is the
    // task's goal or the step's name, because that is the publisher's to say.
    expect(html).toContain(" · Not Defteri");
    expect(html).not.toContain("görev:");
    expect(html).toContain("3 sn önce");
    expect(html).toContain(">çalışıyor<");
    expect(html).not.toContain("attention");
    expect(html).not.toContain("hata sınıfı");
    expect(html).not.toContain("Son bilinen");
  });

  it("says verifying as its own stage", () => {
    const html = panel([OPERATOR_VERIFYING()]);
    expect(html).toContain('data-operator-stage="verifying"');
    expect(html).toContain("Operatör doğruluyor");
    expect(html).toContain(">doğruluyor<");
  });

  it("draws attention to a failure and names the error class the publisher sent", () => {
    const html = panel([OPERATOR_FAILED("focus_mismatch")]);
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-operator-stage="failed"');
    expect(html).toContain("Operatör başarısız");
    expect(html).toContain('data-operator-error-class="focus_mismatch"');
    expect(html).toContain("hata sınıfı: focus_mismatch");
    expect(html).toContain("pencere: Hesap Makinesi");
    expect(html).toContain(">başarısız<");
  });

  it("says when the failure came with no error class rather than inventing one", () => {
    const html = panel([OPERATOR_FAILED(null)]);
    expect(html).toContain('data-operator-error-class=""');
    expect(html).toContain("hata sınıfı bildirilmedi");
  });

  it("says what was not reported when the publisher sent no metadata", () => {
    const html = panel([OPERATOR_RUNNING_BARE()]);
    // No label either: the line ends on the last fact.
    expect(html).toContain("adım bildirilmedi · yetenek bildirilmedi · pencere bildirilmedi</span>");
  });

  it("counts the step the owner's way when the publisher sent its index and the plan's length", () => {
    const html = panel([OPERATOR_RUNNING_INDEXED(1, 3, "type_text", "operator.verifying")]);
    expect(html).toContain('data-operator-stage="verifying"');
    expect(html).toContain('data-operator-step=""');
    expect(html).toContain('data-operator-position="2/3"');
    expect(html).toContain("adım 2/3 · yetenek: app.launch · pencere: Adsız - Not Defteri · type_text");
  });

  it("a task-level failure names the error class and says the step facts were not sent", () => {
    const html = panel([OPERATOR_FAILED_TASK("timeout")]);
    expect(html).toContain('class="panel attention"');
    expect(html).toContain('data-operator-error-class="timeout"');
    expect(html).toContain("hata sınıfı: timeout");
    expect(html).toContain("adım bildirilmedi · yetenek bildirilmedi · pencere bildirilmedi · app_open");
  });

  it("an aged-out step is last-known, not finished", () => {
    const html = panel([OPERATOR_RUNNING()], T0 + OPERATOR_STEP_TTL_MS + 1_000);
    expect(html).toContain('data-operator-stage="none"');
    expect(html).toContain('data-operator-last-known="running"');
    expect(html).toContain("Son bilinen: Operatör çalışıyor");
    expect(html).toContain("Bittiği bildirilmedi");
    expect(html).toContain(">son bilinen<");
    // Still the facts it had: the step is what it WAS doing.
    expect(html).toContain("adım: open_notepad");
  });

  it("a held failure outlives the step horizon", () => {
    const html = panel([OPERATOR_FAILED()], T0 + 24 * 60 * 60_000);
    expect(html).toContain('data-operator-stage="failed"');
    expect(html).toContain("1 gün önce");
  });

  it("the newest operator event is the one shown", () => {
    const html = panel([OPERATOR_FAILED(), OPERATOR_RUNNING()]);
    expect(html).toContain('data-operator-stage="running"');
    expect(html).not.toContain("hata sınıfı");
  });
});

describe("the Core's readout for the operator", () => {
  it("headlines the running operator with the published step as the caption and the facts beneath", () => {
    const html = readout([OPERATOR_RUNNING()]);
    expect(html).toContain('data-core-kind="operator_running"');
    expect(html).toContain('data-core-state="operator.running"');
    expect(html).toContain('data-core-subsystem="operator"');
    expect(html).toContain('data-live="yes"');
    expect(html).toContain("Operatör çalışıyor");
    expect(html).toContain("open_notepad");
    expect(html).toContain("data-operator-facts");
    expect(html).toContain('data-operator-window="Adsız - Not Defteri"');
    expect(html).toContain("pencere: Adsız - Not Defteri");
    expect(html).toContain("Sonuç henüz doğrulanmadı.");
    expect(html).not.toContain("data-operator-error-class");
    // No progress bar: none was published.
    expect(html).not.toContain("core-progress-fill");
    expect(html).toContain("İlerleme bildirilmedi.");
  });

  it("captions an indexed step with its name and states its place in the plan", () => {
    const html = readout([OPERATOR_RUNNING_INDEXED(0, 3, "open_notepad")]);
    expect(html).toContain('data-core-kind="operator_running"');
    expect(html).toContain('data-operator-position="1/3"');
    expect(html).toContain('data-operator-step=""');
    expect(html).toContain("adım 1/3 · yetenek: app.launch");
    // The caption is the bus label, not a voice caption: `data-label` is the
    // boolean attribute (React renders it "true") and no `data-caption` follows.
    expect(html).toContain('data-label="true">open_notepad</p>');
    expect(html).not.toContain("data-caption");
  });

  it("keeps the caption in the compact form and drops the long line", () => {
    const html = readout([OPERATOR_RUNNING()], true);
    expect(html).toContain("open_notepad");
    expect(html).not.toContain("data-operator-facts");
  });

  it("headlines a failure with the error class, in both forms", () => {
    const full = readout([OPERATOR_FAILED("postcondition_failed")]);
    expect(full).toContain('data-core-kind="operator_failed"');
    expect(full).toContain("Operatör başarısız");
    expect(full).toContain('data-operator-error-class="postcondition_failed"');
    expect(full).toContain("hata sınıfı: postcondition_failed");
    expect(full).toContain("type_text");

    const compact = readout([OPERATOR_FAILED("postcondition_failed")], true);
    expect(compact).toContain("hata sınıfı: postcondition_failed");
  });

  it("names the aged-out step as last-known rather than as running", () => {
    const html = readout([OPERATOR_RUNNING()], false, T0 + OPERATOR_STEP_TTL_MS + 1_000);
    expect(html).toContain('data-core-kind="last_known"');
    expect(html).toContain('data-live="no"');
    expect(html).toContain('data-last-state="operator.running"');
    expect(html).toContain("Operatör çalışıyor");
  });
});
