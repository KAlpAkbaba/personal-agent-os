import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  API_BASE: "http://core.test:8001",
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import OfficeScene from "../../app/core/office/OfficeScene";
import OfficeView from "../../app/core/office/OfficeView";
import type { BoardNote } from "../../app/core/office/officeBoard";
import { buildOffice } from "../../app/core/office/officeModel";
import {
  FAMILY_PLAIN_TR,
  plainBreaking,
  plainJobLabel,
  plainSoftwareLabel,
  UNKNOWN_FAMILY_TR,
} from "../../app/core/office/officePlain";
import { TestSeatCells, testRoomFromBoard, testSeatAriaLabel } from "../../app/core/office/officeTestRoom";
import { twoWorkers } from "./fixtures";

// The owner, 2026-10-07 11:50, on a screenshot of the Ofis: "tüm işlemleri yönetici özeti olarak
// görmek istiyorum ... test kısmında hiçbir şey anlamıyorum ... sesli konuşmayı test ediyorum gibi
// bir özet yeterli". Seat labels read 'health (tj-t-w10071102-5)' and breaking points read
// 'kopma: yük 32, 0 hata / 32, p95 13152 ms'; every label must be a plain Turkish summary.

const NOW = new Date("2026-10-07T12:00:00Z");
const SCENARIOS = fileURLToPath(new URL("../../../../scripts/testteam/scenarios/", import.meta.url));

let n = 0;
function note(seat: string, text: string): BoardNote {
  n += 1;
  return {
    id: `n-${String(n).padStart(4, "0")}`,
    at: "2026-10-07T11:50:00Z",
    seat,
    task: "test-team",
    kind: "bilgi",
    to: "",
    reply_to: "",
    text,
  };
}

describe("the Ofis in plain Turkish", () => {
  it("has a plain sentence for every scenario family and id under scripts/testteam/scenarios/", () => {
    const files = readdirSync(SCENARIOS).filter((f) => f.endsWith(".json"));
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      const scenario = JSON.parse(readFileSync(SCENARIOS + file, "utf8")) as {
        id: string;
        family: string;
      };
      for (const name of [scenario.family, scenario.id]) {
        expect(FAMILY_PLAIN_TR[name], `${file}: '${name}' has no plain sentence in officePlain.ts`).toBeTruthy();
        expect(plainJobLabel(`${name} (tj-x-1)`)).not.toBe(UNKNOWN_FAMILY_TR);
      }
    }
  });

  it("names the card's families in the owner's words; an unknown one is a new feature", () => {
    expect(plainJobLabel("saglik (tj-t-w10071102-5)")).toBe("Sistemin yoğun yükte ayakta kalmasını test ediyor");
    expect(plainJobLabel("health (tj-t-w10071102-5)")).toBe("Sistemin yoğun yükte ayakta kalmasını test ediyor");
    expect(plainJobLabel("nobet (tj-t-w10071102-2)")).toBe("Nöbetleri test ediyor");
    expect(plainJobLabel("alarm-saat-ifadeleri (tj-1)")).toBe("Alarm kurarken söylenen saatleri test ediyor");
    expect(plainJobLabel("konusma-devami (tj-1)")).toBe("Konuşmanın kaldığı yerden sürmesini test ediyor");
    expect(plainJobLabel("dil-dayanikliligi (tj-1)")).toBe("Bozuk ve eksik Türkçe cümleleri anlamayı test ediyor");
    expect(plainJobLabel("hic-duyulmamis (tj-1)")).toBe("Yeni bir özelliği test ediyor");
    // the job's own summary_tr wins over the family's sentence
    expect(plainJobLabel("konusma-devami (tj-1): Sesli konuşmayı test ediyor")).toBe("Sesli konuşmayı test ediyor");
  });

  it("never shows the job id or the family slug in a test seat's label", () => {
    const seats = testRoomFromBoard(
      [
        note("tester-1", "iş: health (tj-t-w10071102-5)"),
        note("tester-2", "iş: nobet (tj-t-w10071102-2)"),
        note("tester-3", "sonuç: broke - konusma-devami (tj-t-w10071102-3) - kopma: yük 32, 0 hata / 32, p95 13152 ms"),
      ],
      NOW,
    );
    const html = renderToStaticMarkup(<TestSeatCells seats={seats} now={NOW} animated={false} />);
    const labels = [...html.matchAll(/<span class="office-label"[^>]*>([^<]*)<\/span>/g)].map((m) => m[1]);
    expect(labels).toContain("Sistemin yoğun yükte ayakta kalmasını test ediyor");
    expect(labels).toContain("Nöbetleri test ediyor");
    expect(labels).toContain("Konuşmanın kaldığı yerden sürmesini test ediyor");
    const opening = [...html.matchAll(/<span class="office-(?:label|test-breaking)"[^>]*>/g)].map((m) => m[0]);
    for (const text of [...labels, ...opening]) {
      expect(text).not.toMatch(/tj-|health|nobet|konusma-devami|\(/);
    }
    for (const seat of seats) {
      expect(testSeatAriaLabel(seat, NOW)).not.toMatch(/tj-|health \(|nobet \(|konusma-devami/);
    }
  });

  it("says a breaking point in one plain sentence, with and without errors", () => {
    expect(plainBreaking("kopma: yük 32, 0 hata / 32, p95 13152 ms")).toBe(
      "Aynı anda 32 istekte cevap 13 saniyeye çıkıyor; hata yok, yavaşlıyor",
    );
    expect(plainBreaking("kopma: yük 32, 5 hata / 32, p95 13152 ms")).toBe(
      "Aynı anda 32 istekte cevap 13 saniyeye çıkıyor; 32 istekten 5'i hata veriyor",
    );
    expect(plainBreaking("kopma: yük 64, 2 hata / 64, p95 900 ms")).toBe(
      "Aynı anda 64 istekte cevap 1 saniyenin altında kalıyor; 64 istekten 2'yi hata veriyor",
    );
  });

  it("shows the test lead's long report as its first plain sentence", () => {
    const report =
      "Danışman'a, test turu r7: kopma noktası GET /v1/system/health eşzamanlı - yük 32 ilk kırılan (0 hata / 32, p95 13152 ms). Kırılan 1, kırılmayan 2.";
    expect(plainBreaking(report)).toBe("Aynı anda 32 istekte cevap 13 saniyeye çıkıyor; hata yok, yavaşlıyor");
    expect(plainBreaking("Danışman'a, test turu r7: kırılma bulunmadı; denenen: GET /v1/watches")).toBe(
      "Bu turda sistem zorlanmadı, kırılma bulunmadı",
    );
    const seats = testRoomFromBoard([note("test-lead", report)], NOW);
    const html = renderToStaticMarkup(<TestSeatCells seats={seats} now={NOW} animated={false} />);
    expect(html).toMatch(
      /class="office-test-breaking"[^>]*>Aynı anda 32 istekte cevap 13 saniyeye çıkıyor; hata yok, yavaşlıyor</,
    );
    expect(html).not.toMatch(/class="office-test-breaking"[^>]*>[^<]*Danışman/);
  });

  it("a software seat shows its card's summary_tr, else its title cut at the first ':' or '('", () => {
    expect(plainSoftwareLabel("Ofis sahibe konuşsun: koltuğun üstünde düz Türkçe", "Ofisi düz Türkçe yapıyor")).toBe(
      "Ofisi düz Türkçe yapıyor",
    );
    expect(plainSoftwareLabel("Ofis sahibe yönetici özetiyle konuşsun: koltuğun üstünde teknik iş adı değil")).toBe(
      "Ofis sahibe yönetici özetiyle konuşsun",
    );
    expect(plainSoftwareLabel("Alarm saatleri (Stage 60) düzgün okunsun")).toBe("Alarm saatleri");

    const view = twoWorkers();
    const w1 = view.agents.find((a) => a.seat === "worker-1");
    const w2 = view.agents.find((a) => a.seat === "worker-2");
    if (w1) w1.task_title = "Ofis sahibe yönetici özetiyle konuşsun: koltuğun üstünde teknik iş adı değil";
    if (w2) w2.task_title = "Alarm saatleri: düzeltme (Stage 60)";
    const seats = buildOffice(view, NOW).seats.map((s) =>
      s.seat === "worker-2" ? { ...s, summaryTr: "Alarmı düzeltiyor" } : s,
    );
    const html = renderToStaticMarkup(<OfficeScene seats={seats} selected={null} reducedMotion onSelect={() => {}} />);
    expect(html).toMatch(
      /data-seat="worker-1"[\s\S]*?<span class="office-label"[^>]*>Ofis sahibe yönetici özetiyle konuşsun<\/span>/,
    );
    expect(html).toMatch(/data-seat="worker-2"[\s\S]*?<span class="office-label"[^>]*>Alarmı düzeltiyor<\/span>/);
  });

  it("keeps the technical text in the seat's detail: the software panel and the test seat's details", () => {
    const view = twoWorkers();
    const w1 = view.agents.find((a) => a.seat === "worker-1");
    if (w1) w1.task_title = "Ofis sahibe konuşsun: teknik ayrıntı burada";
    view.tasks["t-one"].title = "Ofis sahibe konuşsun: teknik ayrıntı burada";
    const page = renderToStaticMarkup(
      <OfficeView view={view} selected="worker-1" offline={false} reducedMotion onSelect={() => {}} />,
    );
    expect(page).toContain("Ofis sahibe konuşsun: teknik ayrıntı burada");

    const seats = testRoomFromBoard(
      [note("tester-3", "sonuç: broke - konusma-devami (tj-t-w10071102-3) - kopma: yük 32, 0 hata / 32, p95 13152 ms")],
      NOW,
    );
    const html = renderToStaticMarkup(<TestSeatCells seats={seats} now={NOW} animated={false} />);
    const detail = /<details class="office-test-detail"[^>]*>([\s\S]*?)<\/details>/.exec(html);
    expect(detail, "the test seat's detail").not.toBeNull();
    expect(detail?.[1]).toContain("konusma-devami (tj-t-w10071102-3)");
    expect(detail?.[1]).toContain("kopma: yük 32, 0 hata / 32, p95 13152 ms");
  });
});
