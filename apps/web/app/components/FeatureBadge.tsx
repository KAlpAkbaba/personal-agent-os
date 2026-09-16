/**
 * B24 req 712/713: one badge component, two vocabularies.
 *
 * Both badges render through here so they cannot drift into two different shapes, and
 * both carry the raw class in `data-badge-class` — the vocabulary the FEATURE MATRIX
 * uses. A test asserts that what these render is exactly the document's own class set,
 * which is what "rozetlerin FEATURE_MATRIX ile aynı sınıf sözlüğünü kullandığı" asks for.
 */

import {
  IMPL_LABEL,
  IMPL_TONE,
  PROOF_LABEL,
  PROOF_TONE,
  proofTitle,
} from "../lib/features/badges";
import type { ImplClass, ProofClass } from "../lib/features/matrix.generated";

export function ImplBadge({ impl }: { impl: ImplClass }) {
  return (
    <span
      className={`feature-badge tone-${IMPL_TONE[impl]}`}
      data-badge="impl"
      data-badge-class={impl}
      title={impl}
    >
      {IMPL_LABEL[impl]}
    </span>
  );
}

export function ProofBadge({ proof }: { proof: ProofClass }) {
  return (
    <span
      className={`feature-badge tone-${PROOF_TONE[proof]}`}
      data-badge="proof"
      data-badge-class={proof}
      title={proofTitle(proof)}
    >
      {PROOF_LABEL[proof]}
    </span>
  );
}
