"""Self Model / Code Intelligence: the index that lets the Agent OS describe itself.

Four tables, one job each (``app.selfmodel.models``):

- ``code_modules``     what exists in the checkout, and what kind of thing it is;
- ``code_symbols``     the classes/functions/routes/tables inside a module;
- ``code_edges``       how modules relate (imports, tests, docs, releases);
- ``module_provenance`` the FOUR separate truths about a module -- what the
  source says, what is installed, what is actually running, and what evidence
  rows assert -- kept as one row per ``(module_id, truth_kind)`` so they can
  never collapse into a single optimistic "version".

``app.selfmodel.indexer`` fills the index by static analysis only (no LLM, no
network, never a whole file held in memory beyond the one being parsed).
``app.selfmodel.query`` is the question-shaped read API the Self Explanation
engine (``app.explain``) will call to answer the owner's Turkish questions --
this package returns structured facts with evidence refs and an explicit
confidence, never wording and never a guess.

``app.selfmodel.refresh`` is what keeps all of it true. The index is only worth
consulting if it describes the code that is running, and for the first five days
of its life nothing rebuilt it (ADR-0111).
"""

from __future__ import annotations

#: 1 = PHASE 6, the four tables and the question API.
#: 2 = ADR-0111: the capability layer is actually populated (it held zero rows
#:     under version 1), the ``uses_capability`` edge kind exists, string
#:     constants carry their value, and ``GET /capabilities/{name}`` answers
#:     "which files does this failing capability live in".
SELFMODEL_VERSION = 2

__all__ = ["SELFMODEL_VERSION"]
