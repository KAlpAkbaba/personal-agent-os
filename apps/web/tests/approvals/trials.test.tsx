/**
 * The Onay Merkezi's third list, "Dene" (owner-trials-page; the server half is ADR-0258).
 *
 * Rendered with react-dom/server like the rest of this folder: no DOM, so a click is not
 * exercised. What a click does is `submitTrial` (the row's only path to the server), run here
 * against a mocked `apiFetch`; the row is then rendered in the state it returned.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../app/lib/session", () => ({
  apiFetch: vi.fn(),
  UnauthorizedError: class UnauthorizedError extends Error {},
}));

import { apiFetch } from "../../app/lib/session";
import ApprovalsList from "../../app/core/approvals/ApprovalsList";
import TrialsList, { TrialRow, machineLabel, submitTrial } from "../../app/core/approvals/TrialsList";
import {
  TRIAL_DECISION_PATH,
  waitingCount,
  type OpenTrial,
} from "../../app/core/approvals/approvalsApi";
import { view } from "./fixtures";

const fetchMock = vi.mocked(apiFetch);

function openTrial(over: Partial<OpenTrial["trial"]> = {}, task: Partial<OpenTrial> = {}): OpenTrial {
  return {
    task_id: "app-launch-calc",
    title: "Hesap makinesini aç",
    sha: "e6682a61688d7c4531f5a0ff19de91fd3e780d9d",
    trial: {
      id: "t1",
      sentence: "Hesap makinesini aç",
      machine: "MAIL",
      expect: "Hesap makinesi önde açılır",
      verdict: null,
      said: null,
      at: null,
      ...over,
    },
    ...task,
  };
}

const TWO = [
  openTrial(),
  openTrial(
    {
      id: "t2",
      sentence: "Ofis bilgisayarımdan hesap makinesini aç",
      machine: "MAIL'den söyleyin, ofiste açılacak",
      expect: "Ofis PC'de hesap makinesi açılır",
    },
    { task_id: "app-launch-office", title: "Ofisten aç" },
  ),
];

function reply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function buttons(markup: string, label: string): string[] {
  return [...markup.matchAll(/<button\b[^>]*>([^<]*)<\/button>/g)]
    .filter((match) => match[1] === label)
    .map((match) => match[0]);
}

function row(trial: OpenTrial, initial?: Parameters<typeof TrialRow>[0]["initial"]): string {
  return renderToStaticMarkup(<TrialRow open={trial} locked={false} initial={initial} />);
}

beforeEach(() => fetchMock.mockReset());

describe("the Dene list", () => {
  it("renders two open trials as two rows with sentence, machine, expectation, title and both buttons", () => {
    const markup = renderToStaticMarkup(<TrialsList trials={TWO} locked={false} />);
    expect(markup).toContain("Dene (2)");
    expect(markup.match(/data-trial=/g)).toHaveLength(2);
    expect(markup).toContain("“Hesap makinesini aç”");
    expect(markup).toContain("“Ofis bilgisayarımdan hesap makinesini aç”");
    expect(markup).toContain("MAIL&#x27;de");
    expect(markup).toContain("MAIL&#x27;den söyleyin, ofiste açılacak");
    expect(markup).toContain("Hesap makinesi önde açılır");
    expect(markup).toContain("Ofis PC&#x27;de hesap makinesi açılır");
    expect(markup).toContain("Ofisten aç");
    for (const label of ["Oldu", "Olmadı"]) {
      const found = buttons(markup, label);
      expect(found, label).toHaveLength(2);
      for (const button of found) {
        expect(button).toContain('type="button"');
        expect(button).not.toContain("disabled");
      }
    }
  });

  it("says so when nothing is waiting to be tried", () => {
    const markup = renderToStaticMarkup(<TrialsList trials={[]} locked={false} />);
    expect(markup).toContain("Dene (0)");
    expect(markup).toContain("Denenecek bir şey yok.");
    expect(markup).not.toContain("data-trial=");
  });

  it("sits in the Onay Merkezi beside the approvals, and an older server's view has no list", () => {
    const withTrials = renderToStaticMarkup(<ApprovalsList view={view({ trials: TWO })} onDone={() => {}} />);
    expect(withTrials).toContain("Dene (2)");
    expect(withTrials).toContain('data-approval="stt-soniox-olcum"');
    const older = renderToStaticMarkup(<ApprovalsList view={view()} onDone={() => {}} />);
    expect(older).toContain("Denenecek bir şey yok.");
  });

  it("counts the open trials in what the Onay Merkezi reports as waiting", () => {
    expect(waitingCount(view({ trials: TWO }))).toBe(4);
    expect(waitingCount(view())).toBe(2);
    const markup = renderToStaticMarkup(<ApprovalsList view={view({ trials: TWO })} onDone={() => {}} />);
    expect(markup).toContain("Sizi bekleyen: 4");
  });

  it("locks both buttons while decisions are closed", () => {
    const markup = renderToStaticMarkup(<TrialRow open={TWO[0]} locked />);
    for (const label of ["Oldu", "Olmadı"]) expect(buttons(markup, label)[0]).toContain("disabled");
  });

  it("names the machine with the Turkish locative, and keeps a phrase as written", () => {
    expect(machineLabel("MAIL")).toBe("MAIL'de");
    expect(machineLabel("OFIS")).toBe("OFIS'te");
    expect(machineLabel("LAPTOP")).toBe("LAPTOP'ta");
    expect(machineLabel("MAIL'den söyleyin, ofiste açılacak")).toBe("MAIL'den söyleyin, ofiste açılacak");
  });
});

describe("Oldu", () => {
  it("posts the decision verbatim and the row shows the saved sentence", async () => {
    fetchMock.mockResolvedValueOnce(
      reply(200, { verdict: "oldu", fix_task_id: null, message: "Oldu kaydedildi; PROVEN_REAL satırını lead senin sözünle yazar." }),
    );
    const state = await submitTrial(TWO[0], "oldu", "");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, init] = fetchMock.mock.calls[0]!;
    expect(path).toBe(TRIAL_DECISION_PATH);
    expect(path).toBe("/v1/team/trials/decision");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      task_id: "app-launch-calc",
      trial_id: "t1",
      verdict: "oldu",
      said: null,
    });
    const markup = row(TWO[0], state);
    expect(markup).toContain("Kaydedildi — kanıt satırını lead yazar");
    expect(buttons(markup, "Oldu")).toHaveLength(0);
  });
});

describe("Olmadı", () => {
  it("first opens the 'Ne oldu?' box: required, at most 500 characters, keyboard reachable", () => {
    const markup = row(TWO[0], { phase: "asking" });
    expect(markup).toContain("Ne oldu?");
    const box = /<textarea\b[^>]*>/.exec(markup)?.[0] ?? "";
    expect(box).toContain('maxLength="500"');
    expect(box).toContain("required");
    expect(box).toContain('aria-label="Ne oldu? app-launch-calc t1"');
    expect(buttons(markup, "Gönder")).toHaveLength(1);
  });

  it("without words does not post and shows the required message", async () => {
    const state = await submitTrial(TWO[0], "olmadi", "   ");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(state.phase).toBe("asking");
    expect(row(TWO[0], state)).toContain("Ne olduğunu yazın; boş gönderilemez.");
  });

  it("with words posts them and the row shows the new task's id", async () => {
    fetchMock.mockResolvedValueOnce(
      reply(200, { verdict: "olmadi", fix_task_id: "fix-app-launch-calc-1", message: "Olmadı kaydedildi." }),
    );
    const state = await submitTrial(TWO[0], "olmadi", " açılmadı, not defteri geldi ");
    expect(JSON.parse(String(fetchMock.mock.calls[0]![1]?.body))).toEqual({
      task_id: "app-launch-calc",
      trial_id: "t1",
      verdict: "olmadi",
      said: "açılmadı, not defteri geldi",
    });
    expect(row(TWO[0], state)).toContain("Düzeltme işi kuyruğa girdi: fix-app-launch-calc-1");
  });

  it("refuses more than 500 characters without posting", async () => {
    const state = await submitTrial(TWO[0], "olmadi", "x".repeat(501));
    expect(fetchMock).not.toHaveBeenCalled();
    expect(row(TWO[0], state)).toContain("En çok 500 karakter yazılabilir.");
  });
});

describe("a refusal", () => {
  it("a 409 restores the row with the server's own sentence", async () => {
    fetchMock.mockResolvedValueOnce(
      reply(409, { detail: { code: "already_decided", message: "Bu deneme zaten kararlı: oldu.", verdict: "oldu" } }),
    );
    const state = await submitTrial(TWO[0], "oldu", "");
    expect(state.phase).toBe("open");
    const markup = row(TWO[0], state);
    expect(markup).toContain("Bu deneme zaten kararlı: oldu.");
    expect(markup).toContain('role="alert"');
    expect(buttons(markup, "Oldu")).toHaveLength(1);
    expect(buttons(markup, "Olmadı")).toHaveLength(1);
    expect(markup).not.toContain("Kaydedildi");
  });

  it("a failed post never becomes a success", async () => {
    fetchMock.mockRejectedValueOnce(new Error("ağ yok"));
    const state = await submitTrial(TWO[0], "olmadi", "olmadı");
    expect(state.phase).toBe("asking");
    expect(row(TWO[0], state)).toContain("ağ yok");
  });
});
