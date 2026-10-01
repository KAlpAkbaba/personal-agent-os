/**
 * The Onay Merkezi's "Detay" (owner, 2026-10-01) and deciding while a cycle runs.
 *
 * Rendered to static markup with react-dom/server, like every page test here: no DOM, so
 * the click itself is not exercised - a card is rendered closed (the default) and open
 * (`defaultOpen`), and the markup of each is asserted.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import ApprovalsList, { ApprovalCard, cardButtons } from "../../app/core/approvals/ApprovalsList";
import ProposalDetail from "../../app/core/approvals/ProposalDetail";
import { decisionsOpen, type ApprovalsView } from "../../app/core/approvals/approvalsApi";
import { BOLD_HEADINGS, HOSTILE, WITHOUT_BENEFIT, WITH_BENEFIT, approval, view } from "./fixtures";

const NO_BENEFIT = "Bu öneri fayda örnekleri olmadan yazılmış; araştırmacı bir sonraki koşuda ekleyecek.";
const NO_TEXT = "Bu fikrin metni henüz sunucuya ulaşmadı.";
const DECIDE_NEXT_CYCLE = "Bir döngü çalışıyor; kararınız bir sonraki döngüde uygulanır.";
const DECIDE_REFUSED = "Bir döngü çalışıyor; bitene kadar karar verilemez.";

function list(over: Partial<ApprovalsView> = {}): string {
  return renderToStaticMarkup(<ApprovalsList view={view(over)} onDone={() => {}} />);
}

function card(over: Parameters<typeof approval>[0] = {}, open = true): string {
  return renderToStaticMarkup(
    <ApprovalCard approval={approval(over)} locked={false} onDone={() => {}} defaultOpen={open} />,
  );
}

/** Every `<button …>` opening tag whose text is `label`. */
function buttons(markup: string, label: string): string[] {
  return [...markup.matchAll(/<button\b[^>]*>([^<]*)<\/button>/g)]
    .filter((match) => match[1] === label)
    .map((match) => match[0]);
}

describe("the Detay button", () => {
  it("is a real button on every idea, closed by default, and the closed card shows no proposal", () => {
    const markup = list();
    const detail = buttons(markup, "Detay");
    expect(detail).toHaveLength(2);
    for (const button of detail) {
      expect(button).toContain('type="button"');
      expect(button).toContain('aria-expanded="false"');
      expect(button).not.toContain("disabled");
    }
    expect(markup).not.toContain("data-detail=");
    expect(markup).not.toContain("Bugün");
    expect(markup).not.toContain("Neden şimdi");
    expect(markup).not.toContain("<pre>");
  });

  it("opens a panel it names, and says so to a screen reader", () => {
    const markup = card();
    const [button] = buttons(markup, "Detay");
    expect(button).toContain('aria-expanded="true"');
    const controls = /aria-controls="([^"]+)"/.exec(button)?.[1];
    expect(controls).toBeTruthy();
    expect(markup).toContain(`id="${controls}"`);
    expect(markup).toContain('data-detail="stt-soniox-olcum"');
  });

  it("stays usable while a cycle holds the decision, and is not offered for a release with no proposal", () => {
    const locked = list({ cycle_running: true, decisions_open: false });
    for (const button of buttons(locked, "Detay")) expect(button).not.toContain("disabled");
    expect(buttons(locked, "Detay")).toHaveLength(2);

    const release = card({ gate: "yayin", proposal: null, proposal_text: null }, false);
    expect(buttons(release, "Detay")).toHaveLength(0);
    expect(buttons(release, "Yayını onayla")).toHaveLength(1);
    const releaseOfAnIdea = card({ gate: "yayin" }, false);
    expect(buttons(releaseOfAnIdea, "Detay")).toHaveLength(1);
  });
});

describe("the opened proposal", () => {
  it("shows the benefit pairs first, then Ne, then the rest in the file's order - and no <pre>", () => {
    const markup = renderToStaticMarkup(<ProposalDetail text={WITH_BENEFIT} />);
    expect(markup).not.toContain("<pre");
    const order = [
      "Faydası — örneklerle",
      "ışığı kapa at",
      "komut ilk seferde çalışır",
      "tahmin ediyoruz",
      "tablo raporda durur",
      "Kazanç",
      "Kazanmadığımız",
      "<h3>Ne</h3>",
      "Sahibin cümlesi değişmez",
      "Tarih: 2026-10-01",
      "<h3>Neden şimdi</h3>",
      "<h3>Nasıl</h3>",
      "<h3>Maliyet/risk</h3>",
      "<h3>Deneme notu</h3>",
      "<h3>Kanıt planı</h3>",
      "<h3>Karar</h3>",
    ].map((needle) => [needle, markup.indexOf(needle)] as const);
    for (const [needle, at] of order) expect(at, needle).toBeGreaterThanOrEqual(0);
    expect(order.map(([, at]) => at)).toEqual(order.map(([, at]) => at).toSorted((a, b) => a - b));
    // Each example is one pair: its "Bugün" and its "Bununla", together.
    expect(markup.match(/data-benefit-pair/g)).toHaveLength(3);
    const firstPair = markup.slice(markup.indexOf("data-benefit-pair"), markup.indexOf("tahmin ediyoruz"));
    expect(firstPair).toContain("Bugün");
    expect(firstPair).toContain("Bununla");
    expect(markup).not.toContain(NO_BENEFIT);
    expect(markup).not.toContain(NO_TEXT);
    // The section is not repeated among "the rest".
    expect(markup.split("Faydası — örneklerle")).toHaveLength(2);
  });

  it("renders lists as lists and http links as links that open safely", () => {
    const markup = renderToStaticMarkup(<ProposalDetail text={WITH_BENEFIT} />);
    expect(markup).toContain("<li>Efor: küçük-orta.</li>");
    const links = [...markup.matchAll(/<a\b[^>]*>/g)].map((match) => match[0]);
    expect(links.length).toBeGreaterThanOrEqual(3);
    for (const link of links) {
      expect(link).toMatch(/href="https:\/\/soniox\.com\//);
      expect(link).toMatch(/rel="[^"]*noopener[^"]*"/);
    }
  });

  it("reads the older '**Heading**:' proposals the same way", () => {
    const markup = renderToStaticMarkup(<ProposalDetail text={BOLD_HEADINGS} />);
    expect(markup.indexOf("30 saniyelik özet gelir")).toBeGreaterThan(0);
    expect(markup.indexOf("30 saniyelik özet gelir")).toBeLessThan(markup.indexOf("<h3>Ne</h3>"));
    expect(markup).toContain("<h3>Maliyet/risk</h3>");
    expect(markup).not.toContain("**");
  });

  it("says a proposal was written without benefit examples, and shows the rest", () => {
    const markup = card({ proposal_text: WITHOUT_BENEFIT });
    expect(markup).toContain(NO_BENEFIT);
    expect(markup.indexOf(NO_BENEFIT)).toBeLessThan(markup.indexOf("<h3>Ne</h3>"));
    expect(markup).toContain("Home Assistant&#x27;ı salt-okuma ile bağlamak.");
    expect(markup).toContain("<h3>Karar</h3>");
    expect(markup).not.toContain(NO_TEXT);
  });

  it("says the text has not reached the server - never an empty panel", () => {
    for (const missing of [null, "", "  \n "]) {
      const markup = card({ proposal_text: missing });
      expect(markup).toContain(NO_TEXT);
      expect(markup).not.toContain(NO_BENEFIT);
      expect(markup).not.toContain("<h3>");
    }
  });

  it("renders tags and a javascript: link as plain text, never as markup or href", () => {
    const markup = card({ proposal_text: HOSTILE, title: "x" });
    expect(markup).not.toContain("<script");
    expect(markup).not.toContain("<img");
    expect(markup).not.toContain("<b>");
    expect(markup).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(markup).toContain("&lt;img src=x onerror=alert(2)&gt;");
    // No link at all: the only links in this text are javascript: and data:.
    expect(markup).not.toContain("<a ");
    expect(markup).not.toContain("href=");
    expect(markup).toContain("[tıkla](javascript:void0)");
    expect(markup).toContain("[x](JaVaScRiPt:void0)");
  });
});

describe("deciding while a cycle runs", () => {
  it("reads decisions_open, and falls back to !cycle_running when the field is absent", () => {
    expect(decisionsOpen({ cycle_running: true, decisions_open: true })).toBe(true);
    expect(decisionsOpen({ cycle_running: true, decisions_open: false })).toBe(false);
    expect(decisionsOpen({ cycle_running: false, decisions_open: false })).toBe(false);
    expect(decisionsOpen({ cycle_running: false, decisions_open: true })).toBe(true);
    expect(decisionsOpen({ cycle_running: true })).toBe(false);
    expect(decisionsOpen({ cycle_running: false })).toBe(true);
  });

  it("keeps Onayla enabled while a cycle runs and decisions are open, and says when it applies", () => {
    const markup = list({ cycle_running: true, decisions_open: true });
    const approve = buttons(markup, "Onayla");
    expect(approve).toHaveLength(2);
    for (const button of approve) expect(button).not.toContain("disabled");
    expect(markup).toContain(DECIDE_NEXT_CYCLE);
    expect(markup).not.toContain(DECIDE_REFUSED);
    expect(markup).not.toContain("karar verilemez");
  });

  it("disables the decision when decisions_open is false, running or not", () => {
    const running = list({ cycle_running: true, decisions_open: false });
    for (const label of ["Onayla", "Reddet"]) {
      expect(buttons(running, label)).toHaveLength(2);
      for (const button of buttons(running, label)) expect(button).toContain('disabled=""');
    }
    expect(running).toContain(DECIDE_REFUSED);
    expect(running).not.toContain(DECIDE_NEXT_CYCLE);

    const idle = list({ cycle_running: false, decisions_open: false });
    for (const button of buttons(idle, "Onayla")) expect(button).toContain('disabled=""');
    expect(idle).toContain("karar verilemez"); // a disabled button is never left unexplained
    expect(idle).not.toContain("Bir döngü çalışıyor");
  });

  it("holds the old behaviour when the field is absent", () => {
    const running = list({ cycle_running: true });
    for (const button of buttons(running, "Onayla")) expect(button).toContain('disabled=""');
    expect(running).toContain(DECIDE_REFUSED);
    expect(running).not.toContain(DECIDE_NEXT_CYCLE);

    const idle = list({ cycle_running: false });
    for (const button of buttons(idle, "Onayla")) expect(button).not.toContain("disabled");
    expect(idle).not.toContain("Bir döngü çalışıyor");
    expect(idle).not.toContain("karar verilemez");
  });

  it("locks both buttons together, and Reddet alone on an empty reason", () => {
    expect(cardButtons({ busy: false, locked: false, reason: "gerek yok" })).toEqual({
      approveDisabled: false,
      rejectDisabled: false,
    });
    expect(cardButtons({ busy: false, locked: false, reason: "  " })).toEqual({
      approveDisabled: false,
      rejectDisabled: true,
    });
    expect(cardButtons({ busy: false, locked: true, reason: "gerek yok" })).toEqual({
      approveDisabled: true,
      rejectDisabled: true,
    });
    expect(cardButtons({ busy: true, locked: false, reason: "gerek yok" })).toEqual({
      approveDisabled: true,
      rejectDisabled: true,
    });
  });
});

describe("what the page already showed", () => {
  it("still lists the gate, the title, the goal, the reports and the cycle report", () => {
    const markup = renderToStaticMarkup(
      <ApprovalsList
        view={view({
          approvals: [
            approval({
              gate: "yayin",
              reports: [{ cycle: "d20261001", role: "inspector", at: "t", file: "f", summary: ["35/35", "yeşil"] }],
            }),
          ],
          cycle_report: { file: "cycle-d20261001.md", text: "rapor metni" },
        })}
        onDone={() => {}}
      />,
    );
    expect(markup).toContain("Yayın onayı · Soniox&#x27;u ölç");
    expect(markup).toContain("Türkçe STT adayını ölçmek");
    expect(markup).toContain("inspector · d20261001");
    expect(markup).toContain("35/35\nyeşil");
    expect(markup).toContain("Onay yalnızca kuyruğa yazılır; yayını başlatmaz.");
    expect(markup).toContain("Döngü raporu · cycle-d20261001.md");
    expect(markup).toContain("rapor metni");
    expect(renderToStaticMarkup(<ApprovalsList view={view({ approvals: [] })} onDone={() => {}} />)).toContain(
      "Bekleyen onay yok.",
    );
  });
});
