/**
 * B34 (req 160, 162, 166): the third pending source - proposed file changes - on the
 * Cockpit. The parser reads the row verbatim, the paths are the Cloud Core's, the pair is
 * the same one drafts and proposals use, and the Documents panel lists what waits with the
 * sentence the owner heard and the hash of what is there now.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DocumentsPanel } from "../../app/core/panels/CockpitPanels";
import {
  DOCUMENT_MUTATIONS_PENDING_PATH,
  MUTATION_STATE_TR,
  type PendingMutation,
  approvalFamily,
  mutationConfirmPath,
  mutationDiscardPath,
  parseMutation,
} from "../../app/lib/cockpit/approvals";
import { OUTCOME_NO_STATE_TR, outcomeText } from "../../app/lib/cockpit/useApprovalPair";
import { emptyTruth } from "../../app/lib/uistate/truth";

const ROW = {
  mutation_id: "9b1d0c6e-0000-4000-8000-000000000001",
  kind: "edit",
  risk: "sensitive",
  name: "notlar.md",
  path_before: "C:\\Users\\owner\\Documents\\notlar.md",
  path_after: null,
  sha_before: "5cf1c183cc498f941d88ef2d7baa071fbbd24382043062a793d79e141ae295db",
  sha_after: null,
  summary: "notlar dosyasında 'bütçe' yerine 'tahmin' yazacağım efendim (1 yerde).",
  state: "proposed",
  read_back_at: "2026-09-15T10:00:00Z",
  confirmed_at: null,
  created_at: "2026-09-15T10:00:00Z",
};

describe("the pending file changes", () => {
  it("are read verbatim from the row and refused without an id", () => {
    const parsed = parseMutation(ROW);
    expect(parsed).not.toBeNull();
    expect(parsed?.kind).toBe("edit");
    expect(parsed?.summary).toContain("yerine");
    expect(parsed?.sha_before).toBe(ROW.sha_before);
    expect(parseMutation({ kind: "edit" })).toBeNull();
    expect(parseMutation(null)).toBeNull();
  });

  it("confirm and discard through the Cloud Core's own routes, and belong to their own family", () => {
    expect(DOCUMENT_MUTATIONS_PENDING_PATH).toBe("/v1/documents/mutations/pending");
    expect(mutationConfirmPath(ROW.mutation_id)).toBe(`/v1/documents/mutations/${ROW.mutation_id}/confirm`);
    expect(mutationDiscardPath("a b")).toBe("/v1/documents/mutations/a%20b/discard");
    expect(approvalFamily("confirm_mutation")).toBe("mutation");
    expect(approvalFamily("discard_mutation")).toBe("mutation");
    expect(approvalFamily("confirm_draft")).toBe("draft");
    expect(approvalFamily("discard_proposal")).toBe("proposal");
  });

  it("speak the receipt's state in Turkish, and say when the receipt named none", () => {
    expect(outcomeText("confirm_mutation", { state: "applied", summary: null, ok: true } as never)).toBe(
      MUTATION_STATE_TR.applied,
    );
    expect(outcomeText("discard_mutation", { state: "discarded", summary: "notlar", ok: true } as never)).toBe(
      `${MUTATION_STATE_TR.discarded} · notlar`,
    );
    expect(outcomeText("confirm_mutation", { state: null, summary: null, ok: true } as never)).toBe(
      OUTCOME_NO_STATE_TR.confirm_mutation,
    );
  });
});

describe("the Documents panel with a proposed change", () => {
  const pair = { busy: null, outcome: null, onConfirm: () => {}, onDiscard: () => {} };

  it("lists the change with the owner's sentence, the hash of what is there, and the pair", () => {
    const pending = { kind: "ok" as const, value: [parseMutation(ROW) as PendingMutation] };
    const html = renderToStaticMarkup(
      <DocumentsPanel truth={emptyTruth()} now={Date.parse("2026-09-15T10:01:00Z")} pending={{ ...pending, at: 0 }} pair={pair} />,
    );
    expect(html).toContain('data-document-mutations="1"');
    expect(html).toContain(`data-mutation="${ROW.mutation_id}"`);
    expect(html).toContain('data-mutation-kind="edit"');
    expect(html).toContain('data-mutation-risk="sensitive"');
    expect(html).toContain("yerine");
    expect(html).toContain("önce: 5cf1c183cc49");
    expect(html).toContain('data-approval-family="mutation"');
    expect(html).toContain("Onayla — uygula");
    expect(html).toContain('data-approval-enabled="yes"');
  });

  it("goes quiet when nothing waits and nothing was told (req 714), and keeps the pair off a row not read back", () => {
    const none = renderToStaticMarkup(
      <DocumentsPanel truth={emptyTruth()} now={0} pending={{ kind: "ok", value: [], at: 0 }} pair={pair} />,
    );
    expect(none).toBe("");
    // A list still loading keeps the panel: the question is in flight.
    const loading = renderToStaticMarkup(
      <DocumentsPanel truth={emptyTruth()} now={0} pending={{ kind: "loading" }} pair={pair} />,
    );
    expect(loading).toContain('id="documents"');
    expect(loading).toContain("Yükleniyor.");

    const unread = parseMutation({ ...ROW, read_back_at: null }) as PendingMutation;
    const html = renderToStaticMarkup(
      <DocumentsPanel truth={emptyTruth()} now={0} pending={{ kind: "ok", value: [unread], at: 0 }} pair={pair} />,
    );
    expect(html).toContain('data-approval-enabled="no"');
    expect(html).toContain("Önce sesli okunması gerekir");
  });

  it("renders as before when the page does not load the pending source", () => {
    const html = renderToStaticMarkup(<DocumentsPanel truth={emptyTruth()} now={0} />);
    expect(html).not.toContain("data-document-mutations-list");
    expect(html).toContain('data-document-mutations=""');
  });
});
