"""File & Document Intelligence, Cloud Core half (docs/M20_FILE_DOCUMENT_INTELLIGENCE_SPEC.md).

The owner's files stay on the owner's machine (ADR-0083): this package indexes what the
device already extracted (never crawls, never receives a copy of the disk), retrieves
blocks deterministically, and answers with a reference into the source document. A claim
without a ref is not an answer.
"""

from __future__ import annotations
